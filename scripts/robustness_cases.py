"""Robustness cases for the V4.5 gate: where dependency analysis can fail.

Kept separate from the V4.5 held-out set. Each case records the real change, what
static analysis can see, the evidence obligation it should create, the expected gate
status for a given delivery, and whether the current gate produces a false
sufficient.

Evidence profiles are (name, files-to-deliver, expected status). Files are delivered
in full through TrustedCodebase, so the registry is built by the same code the agent
uses. No model is called and no gold path enters the derivation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

FIXED_DATE = "2026-01-01T00:00:00+0000"

BASE = {
    "src/__init__.py": "",
    "src/fmt.py": '''"""Formatting."""


def render(rows, sep=","):
    """Join values into one line."""
    return sep.join(str(row) for row in rows)


def announce(value=1):
    """Return an announcement."""
    return "value " + str(value)
''',
    "src/other.py": '''"""A second formatter with the same function name."""


def render(rows, sep=","):
    """Join values in a different way."""
    return "-".join(str(row) for row in rows)
''',
    "src/report.py": '''"""Report."""

from .fmt import announce, render


def build(rows):
    """Return a one-line report."""
    return render(rows)


def greeting():
    """Return a greeting that relies on the default value."""
    return announce()
''',
    "src/alias_report.py": '''"""Report using an import alias."""

from .fmt import render as r


def build(rows):
    """Return a one-line report."""
    return r(rows)
''',
    "src/parser.py": '''"""Parser."""


def parse(text):
    """Return the number of characters."""
    return len(text)


def load(text):
    """Load parsed content."""
    return parse(text)


def guarded(text):
    """Load a document, reporting malformed input."""
    try:
        return load(text)
    except ValueError:
        return 0
''',
    "src/cart.py": '''"""Cart."""


class Cart:
    """A shopping cart."""

    def price(self, net):
        """Return the price."""
        return net

    def total(self, net):
        """Return the total, using the price."""
        return self.price(net) * 2
''',
    "src/uses_other.py": '''"""Consumer of the second formatter."""

from .other import render


def show(rows):
    """Return a dash-joined line."""
    return render(rows)
''',
    "src/registry.py": '''"""Handler registry."""

HANDLERS = {}


def register(func):
    """Register a handler by name."""
    HANDLERS[func.__name__] = func
    return func


@register
def handler(payload):
    """Handle one payload."""
    return payload
''',
    "src/consumer.py": '''"""Consumer."""

from .registry import HANDLERS


def dispatch(name, payload):
    """Call a registered handler."""
    return HANDLERS[name](payload)
''',
}

HEAD = {
    # R1: only deleted lines inside one function
    "R1-delete-line": {
        "file": "src/fmt.py",
        "body": '''"""Formatting."""


def render(rows, sep=","):
    """Join values into one line."""
    return sep.join([str(row) for row in rows])


def announce(value=1):
    """Return an announcement."""
    return "value " + str(value)
''',
        "note": "a list comprehension replaces the generator; behaviour is the same",
    },
    # R2: a function is deleted
    "R2-delete-function": {
        "file": "src/fmt.py",
        "body": '''"""Formatting."""


def render(rows, sep=","):
    """Join values into one line."""
    return sep.join(str(row) for row in rows)
''',
        "note": "announce() is gone, but a consumer of the other function remains",
    },
    # R3: an entire source file is deleted
    "R3-delete-file": {
        "delete": "src/other.py",
        "note": "the module is deleted while another module still imports it",
    },
    # R4: the callee's signature changes; the caller imports it under an alias
    "R4-alias": {
        "file": "src/fmt.py",
        "body": '''"""Formatting."""


def render(rows, sep=",", prefix=""):
    """Join values into one line, optionally prefixed."""
    return prefix + sep.join(str(row) for row in rows)


def announce(value=1):
    """Return an announcement."""
    return "value " + str(value)
''',
        "note": "one caller uses `render`, another uses the alias `r`",
    },
    # R5: the changed function shares its name with one in another module
    "R5-same-name": {
        "file": "src/fmt.py",
        "body": '''"""Formatting."""


def render(first, second):
    """Join two values."""
    return str(first) + "," + str(second)


def announce(value=1):
    """Return an announcement."""
    return "value " + str(value)
''',
        "note": "render() now takes two required arguments; other.py also defines render",
    },
    # R6: a method's signature changes, called through self
    "R6-method": {
        "file": "src/cart.py",
        "body": '''"""Cart."""


class Cart:
    """A shopping cart."""

    def price(self, net, rate):
        """Return the price with a rate."""
        return net * rate

    def total(self, net):
        """Return the total, using the price."""
        return self.price(net, 2) * 2
''',
        "note": "price() gains a required argument and is called via self.price",
    },
    # R7: an exception is raised two hops away from the handler
    "R7-indirect-raise": {
        "file": "src/parser.py",
        "body": '''"""Parser."""


def parse(text):
    """Return the number of characters."""
    if not text:
        raise LookupError("empty")
    return len(text)


def load(text):
    """Load parsed content."""
    return parse(text)


def guarded(text):
    """Load a document, reporting malformed input."""
    try:
        return load(text)
    except ValueError:
        return 0
''',
        "note": "parse raises a new type; only guarded() catches anything, two hops away",
    },
    # R8: same return shape, different behaviour
    "R8-magnitude": {
        "file": "src/cart.py",
        "body": '''"""Cart."""


class Cart:
    """A shopping cart."""

    def price(self, net):
        """Return the price."""
        return net * 3

    def total(self, net):
        """Return the total, using the price."""
        return self.price(net) * 2
''',
        "note": "price() returns a different magnitude; the contract is unchanged",
    },
    # R9: a default parameter value changes
    "R9-default": {
        "file": "src/fmt.py",
        "body": '''"""Formatting."""


def render(rows, sep=","):
    """Join values into one line."""
    return sep.join(str(row) for row in rows)


def announce(value=2):
    """Return an announcement."""
    return "value " + str(value)
''',
        "note": "announce() default changes from 1 to 2; callers may omit it",
    },
    # R10: registration through a decorator and a registry dict
    "R10-registry": {
        "file": "src/registry.py",
        "body": '''"""Handler registry."""

HANDLERS = {}


def register(func):
    """Register a handler by name."""
    HANDLERS[func.__name__] = func
    return func


@register
def handler(payload):
    """Handle one payload, now with a guard."""
    if payload is None:
        return {}
    return payload
''',
        "note": "the handler is dispatched by name through HANDLERS; no static caller",
    },
}

CASES = [
    {"id": "R1-delete-line", "head": "R1-delete-line",
     "expect_kinds": ["modified_definition"],
     "note": "a deleted line must still create a duty on the changed definition"},
    {"id": "R2-delete-function", "head": "R2-delete-function",
     "expect_kinds": ["caller_usage"],
     "note": "removing a definition can break callers"},
    {"id": "R3-delete-file", "head": "R3-delete-file",
     "expect_kinds": ["caller_usage"],
     "note": ("a deleted module's importers must be read; import-aware matching "
              "finds them, so this does not have to be unknown")},
    {"id": "R4-alias", "head": "R4-alias",
     "expect_kinds": ["caller_usage"],
     "note": "callers reach the callee through an import alias"},
    {"id": "R5-same-name", "head": "R5-same-name",
     "expect_kinds": ["caller_usage"],
     "note": "the caller imports the changed module, not the same-named other module"},
    {"id": "R6-method", "head": "R6-method",
     "expect_kinds": ["caller_usage"],
     "note": "the caller is in the same class, reached through self.price"},
    {"id": "R7-indirect-raise", "head": "R7-indirect-raise",
     "expect_kinds": ["unknown_dependency"],
     "note": "the exception handler is two hops away; static reachability is limited"},
    {"id": "R8-magnitude", "head": "R8-magnitude",
     "expect_kinds": ["modified_definition"],
     "note": "a magnitude change is a local duty, not a dependency question"},
    {"id": "R9-default", "head": "R9-default",
     "expect_kinds": ["caller_usage"],
     "note": "changing a default changes the contract for callers that omit it"},
    {"id": "R10-registry", "head": "R10-registry",
     "expect_kinds": ["unknown_dependency"],
     "note": "dispatch happens through a registry; callers cannot be enumerated"},
]

# delivered evidence profile -> expected status, given a sound gate
PROFILES = {
    "R1-delete-line": [("changed-file", ["src/fmt.py"], "sufficient"),
                       ("nothing", [], "insufficient")],
    "R2-delete-function": [("changed-file", ["src/fmt.py"], "insufficient"),
                           ("plus-consumer", ["src/fmt.py", "src/report.py"],
                            "sufficient")],
    "R3-delete-file": [("importer", ["src/uses_other.py"], "sufficient"),
                       ("nothing", [], "insufficient")],
    "R4-alias": [("changed-file", ["src/fmt.py"], "insufficient"),
                 ("plus-callers", ["src/fmt.py", "src/report.py",
                                   "src/alias_report.py"], "sufficient")],
    "R5-same-name": [("changed-file", ["src/fmt.py"], "insufficient"),
                     ("plus-callers", ["src/fmt.py", "src/report.py",
                                       "src/alias_report.py"], "sufficient")],
    "R6-method": [("changed-file", ["src/cart.py"], "sufficient")],
    "R7-indirect-raise": [("changed-file", ["src/parser.py"], "unknown")],
    "R8-magnitude": [("changed-file", ["src/cart.py"], "sufficient")],
    "R9-default": [("changed-file", ["src/fmt.py"], "insufficient"),
                   ("plus-callers", ["src/fmt.py", "src/report.py",
                                     "src/alias_report.py"], "sufficient")],
    "R10-registry": [("changed-file", ["src/registry.py"], "unknown")],
}


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE,
               GIT_AUTHOR_NAME="rob", GIT_AUTHOR_EMAIL="rob@example.com",
               GIT_COMMITTER_NAME="rob", GIT_COMMITTER_EMAIL="rob@example.com")
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def build_case(root: Path, case: dict) -> Path:
    repo = root / case["id"]
    if repo.exists():
        return repo
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "rob@example.com")
    _git(repo, "config", "user.name", "rob")
    for rel, body in BASE.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    branch = "case/" + case["id"].lower()
    _git(repo, "checkout", "-q", "-b", branch)
    change = HEAD[case["head"]]
    if "delete" in change:
        (repo / change["delete"]).unlink()
    else:
        (repo / change["file"]).write_text(change["body"], encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qam", case["id"])
    _git(repo, "checkout", "-q", "main")
    return repo


def freeze(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    payload = {"frozen": True, "cases": [], "profiles": PROFILES,
               "note": ("Robustness set, separate from the V4.5 held-out set. "
                        "Expected kinds and statuses were declared before running "
                        "the gate against this set.")}
    for case in CASES:
        repo = build_case(root, case)
        branch = "case/" + case["id"].lower()
        payload["cases"].append({
            **case, "branch": branch,
            "sha_head": _git(repo, "rev-parse", branch).strip(),
        })
    (out / "cases.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    return payload
