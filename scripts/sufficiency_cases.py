"""Held-out cases for the V4.5 sufficiency gate.

Six new repositories, none of them used while designing the obligation model or the
assessor. Each case declares:

* the repository content and head branch;
* the *expected obligation kinds* (an annotation of the diff's duties, written from
  the change semantics, not from the gate's output);
* evidence profiles: which ranges are delivered, and what the gate should answer.

Profiles are executed against the real snapshot through `TrustedCodebase`, so the
evidence registry is built by the same code the agent uses. No model is called.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

FIXED_DATE = "2026-01-01T00:00:00+0000"

BASE = {
    "src/__init__.py": "",
    "src/formatting.py": '''"""Formatting."""


def render(rows, sep=","):
    """Join values into one line."""
    return sep.join(str(row) for row in rows)
''',
    "src/limits.py": '''"""Limits."""

MAX_ITEMS = 100


def accept(count):
    """Return whether a count is within the limit."""
    if count > MAX_ITEMS:
        return False
    return True
''',
    "src/report.py": '''"""Report."""

from .formatting import render


def build(rows):
    """Return a one-line report."""
    return render(rows)


def build_many(groups):
    """Return one line per group."""
    return [render(group) for group in groups]
''',
    "src/queue.py": '''"""Queue."""


def drain(items):
    """Return the remaining items after processing."""
    return items[1:]
''',
    "src/cart.py": '''"""Cart."""


def price(net):
    """Return the price of a basket."""
    return net


def receipt(net):
    """Return a receipt line."""
    return "total " + str(price(net))
''',
    "src/plugin.py": '''"""Plugin loading."""

HANDLERS = {}


def dispatch(name, payload):
    """Call a registered handler by name."""
    handler = HANDLERS[name]
    return handler(payload)
''',
}

HEAD = {
    # a required parameter is added to the callee; both callers must be checked
    "H1-signature": {
        "file": "src/formatting.py",
        "body": '''"""Formatting."""


def render(rows, sep=",", prefix=""):
    """Join values into one line, optionally prefixed."""
    return prefix + sep.join(str(row) for row in rows)
''',
    },
    # the return contract changes from str to list
    "H2-return": {
        "file": "src/formatting.py",
        "body": '''"""Formatting."""


def render(rows, sep=","):
    """Return the values as a list of strings."""
    return [str(row) for row in rows]
''',
    },
    # dynamic dispatch: static analysis cannot enumerate the targets
    "H3-dynamic": {
        "file": "src/plugin.py",
        "body": '''"""Plugin loading."""

HANDLERS = {}


def dispatch(name, payload):
    """Call a registered handler by name, falling back to getattr."""
    handler = getattr(payload, name, None)
    return handler()
''',
    },
    # two callers exist; a profile can read only one
    "H4-multicaller": {
        "file": "src/report.py",
        "body": '''"""Report."""

from .formatting import render


def build(rows):
    """Return a one-line report."""
    return render(rows, sep=";")


def build_many(groups):
    """Return one line per group."""
    return [render(group, " ") for group in groups]
''',
    },
    # a cross-file configuration constant changes
    "H5-config": {
        "file": "src/limits.py",
        "body": '''"""Limits."""

MAX_ITEMS = 10


def accept(count):
    """Return whether a count is within the limit."""
    if count > MAX_ITEMS:
        return False
    return True
''',
    },
    # a required parameter is added, and the only caller is in the SAME file
    "H7-same-file": {
        "file": "src/cart.py",
        "body": '''"""Cart."""


def price(net, currency):
    """Return the price of a basket in a currency."""
    return str(net) + " " + currency


def receipt(net):
    """Return a receipt line."""
    return "total " + str(price(net))
''',
    },
    # a purely local change inside one definition
    "H6-local": {
        "file": "src/queue.py",
        "body": '''"""Queue."""


def drain(items):
    """Return the remaining items after processing the first two."""
    return items[2:]
''',
    },
}

# expected obligation *kinds*, annotated from the change semantics before running
CASES = [
    {"id": "H1-signature", "head": "H1-signature",
     "expected_kinds": ["modified_definition", "caller_usage"],
     "note": "a required parameter is added; existing callers must be read"},
    {"id": "H2-return", "head": "H2-return",
     "expected_kinds": ["modified_definition", "caller_usage"],
     "note": "render now returns a list; callers expect a string"},
    {"id": "H3-dynamic", "head": "H3-dynamic",
     "expected_kinds": ["modified_definition", "unknown_dependency"],
     "note": "getattr dispatch cannot be enumerated statically"},
    {"id": "H4-multicaller", "head": "H4-multicaller",
     "expected_kinds": ["modified_definition", "callee_contract"],
     "note": "the change calls render with new arguments"},
    {"id": "H5-config", "head": "H5-config",
     "expected_kinds": ["modified_definition"],
     "note": "the constant lives in the changed file, so no cross-file read is due"},
    {"id": "H6-local", "head": "H6-local",
     "expected_kinds": ["modified_definition"],
     "note": "a local behaviour change with no cross-file dependency"},
    {"id": "H7-same-file", "head": "H7-same-file",
     "expected_kinds": ["modified_definition", "caller_usage"],
     "note": "the caller is in the same file; a sound gate must still demand it"},
]

# evidence profiles: (name, files to deliver in full, expected gate status)
PROFILES = {
    "H1-signature": [
        ("complete", ["src/formatting.py", "src/report.py"], "sufficient"),
        ("callee-only", ["src/formatting.py"], "insufficient"),
    ],
    "H2-return": [
        ("complete", ["src/formatting.py", "src/report.py"], "sufficient"),
        ("callee-only", ["src/formatting.py"], "insufficient"),
    ],
    "H3-dynamic": [
        ("complete", ["src/plugin.py"], "unknown"),
    ],
    "H4-multicaller": [
        ("complete", ["src/report.py", "src/formatting.py"], "sufficient"),
        ("one-caller", ["src/report.py"], "insufficient"),
    ],
    "H5-config": [
        ("complete", ["src/limits.py"], "sufficient"),
    ],
    "H6-local": [
        ("complete", ["src/queue.py"], "sufficient"),
        ("search-only", [], "insufficient"),
    ],
    "H7-same-file": [
        ("complete", ["src/cart.py"], "sufficient"),
        # only the changed function is delivered; the same-file caller is not
        ("callee-only", [("src/cart.py", 4, 6)], "insufficient"),
    ],
}


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE,
               GIT_AUTHOR_NAME="held", GIT_AUTHOR_EMAIL="held@example.com",
               GIT_COMMITTER_NAME="held", GIT_COMMITTER_EMAIL="held@example.com")
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def build_case(root: Path, case: dict) -> Path:
    repo = root / case["id"]
    if repo.exists():
        return repo
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "held@example.com")
    _git(repo, "config", "user.name", "held")
    for rel, body in BASE.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    branch = "case/" + case["id"].lower()
    _git(repo, "checkout", "-q", "-b", branch)
    change = HEAD[case["head"]]
    (repo / change["file"]).write_text(change["body"], encoding="utf-8")
    _git(repo, "commit", "-qam", case["id"])
    _git(repo, "checkout", "-q", "main")
    return repo


def freeze(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    payload = {"frozen": True, "cases": [], "profiles": PROFILES,
               "note": ("Held-out cases for V4.5. Expected kinds are annotated from "
                        "the change semantics; profiles state which files are "
                        "delivered and the status the gate should return.")}
    for case in CASES:
        repo = build_case(root, case)
        payload["cases"].append({
            **case,
            "branch": "case/" + case["id"].lower(),
            "sha_head": _git(repo, "rev-parse", "case/" + case["id"].lower()).strip(),
            "sha_main": _git(repo, "rev-parse", "main").strip(),
        })
    (out / "cases.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    return payload
