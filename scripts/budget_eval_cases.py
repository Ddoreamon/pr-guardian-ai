"""Frozen V4.3 dev cases: three cross-file bugs and three clean PRs.

The point of this file is to *fix* the evaluation before any optimisation exists,
so a later cache or planner cannot be judged against a moving target.

Each case defines: the repository content, the base and head branch, the gold
location (file + qualname + line), the evidence a reviewer must actually obtain to
judge it, and the budgets. Nothing here calls a model; `--freeze` writes the
machine-readable definition and a readable summary.

Run:
    python scripts/budget_eval_cases.py --freeze
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

MAX_TOOL_CALLS = 4
MAX_ROUNDS = 3
TOP_K = 5

# ---------------------------------------------------------------------------
# repository building blocks
# ---------------------------------------------------------------------------

BASE_FILES = {
    "src/__init__.py": "",
    "src/shipping.py": '''"""Shipping."""


def estimate(weight_kg):
    """Return the shipping cost in dollars for a parcel weight."""
    return round(4.5 + weight_kg * 1.25, 2)
''',
    "src/money.py": '''"""Money."""


def to_cents(dollars):
    """Convert dollars to integer cents."""
    return int(round(dollars * 100))
''',
    "src/stock.py": '''"""Stock."""


def reserve(available, requested):
    """Reserve stock, raising when there is not enough."""
    if requested > available:
        raise ValueError("insufficient stock")
    return available - requested
''',
    "src/checkout.py": """\"\"\"Checkout.\"\"\"

from .money import to_cents
from .shipping import estimate


def total(weight_kg, net):
    \"\"\"Return the order total.\"\"\"
    return round(net + estimate(weight_kg), 2)
""",
}

HEAD_FILES = {
    # B1: cents value passed where whole units are expected
    "bug_cents": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(to_cents(weight_kg)), 2)
''',
    # B2: a keyword the callee does not accept
    "bug_kwarg": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg, express=True), 2)
''',
    # B3: exception type changes and the caller catches the old one
    "bug_exception": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def place(available, requested):
    """Place an order, reporting a shortage as a validation error."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0
''',
    # C1: an extra keyword the callee accepts
    "clean_kwarg": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg), 2)


def label(weight_kg, net):
    """Return a short label."""
    return "order " + str(total(weight_kg, net))
''',
    # C2: conversion is the identity for the value used
    "clean_identity": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg), 2)


def cents_of(dollars):
    """Return the value in cents."""
    return to_cents(dollars)
''',
    # C3: the guard already rejects the risky input upstream
    "clean_guard": '''"""Stock."""

MIN_REQUEST = 1


def reserve(available, requested):
    """Reserve stock, raising when there is not enough."""
    if requested < MIN_REQUEST:
        raise ValueError("requested must be positive")
    if requested > available:
        raise ValueError("insufficient stock")
    return available - requested
''',
}

CASES = [
    {
        "id": "B1-cents",
        "label": "bug",
        "branch": "case/b1",
        "head_file": "src/checkout.py",
        "head_body": "bug_cents",
        "gold": {"file_path": "src/shipping.py", "qualname": "estimate", "line": 4},
        "required_evidence": ["src/shipping.py:1-8", "src/checkout.py:1-10"],
        "why": "a cents value reaches a dollars parameter; only the callee's "
               "docstring decides it",
    },
    {
        "id": "B2-kwarg",
        "label": "bug",
        "branch": "case/b2",
        "head_file": "src/checkout.py",
        "head_body": "bug_kwarg",
        "gold": {"file_path": "src/shipping.py", "qualname": "estimate", "line": 4},
        "required_evidence": ["src/shipping.py:1-8"],
        "why": "estimate() takes no express keyword, so the call raises at runtime",
    },
    {
        "id": "B3-exception",
        "label": "bug",
        "branch": "case/b3",
        "head_file": "src/checkout.py",
        "head_body": "bug_exception",
        "gold": {"file_path": "src/stock.py", "qualname": "reserve", "line": 4},
        "required_evidence": ["src/stock.py:1-8", "src/checkout.py:1-12"],
        "why": "a raised exception escapes the caller's except clause; the callee "
               "body is required to see it",
    },
    {
        "id": "C1-kwarg",
        "label": "clean",
        "branch": "case/c1",
        "head_file": "src/checkout.py",
        "head_body": "clean_kwarg",
        "gold": {"file_path": "src/checkout.py", "qualname": "label", "line": 12},
        "required_evidence": ["src/checkout.py:1-14", "src/shipping.py:1-8"],
        "why": "the new caller is fine; a reviewer must still read it to confirm",
    },
    {
        "id": "C2-identity",
        "label": "clean",
        "branch": "case/c2",
        "head_file": "src/checkout.py",
        "head_body": "clean_identity",
        "gold": {"file_path": "src/money.py", "qualname": "to_cents", "line": 4},
        "required_evidence": ["src/money.py:1-6", "src/checkout.py:9-12"],
        "why": "a conversion looks risky until the callee confirms it is applied once",
    },
    {
        "id": "C3-guard",
        "label": "clean",
        "branch": "case/c3",
        "head_file": "src/stock.py",
        "head_body": "clean_guard",
        "gold": {"file_path": "src/stock.py", "qualname": "reserve", "line": 4},
        "required_evidence": ["src/stock.py:1-10"],
        "why": "an added guard is harmless; the reviewer must read it to prove that",
    },
]

METRICS = [
    "tool_calls_requested", "tool_calls_executed", "tool_calls_denied",
    "invalid_argument_calls", "empty_searches", "exact_repeat_calls",
    "overlapping_read_calls", "required_evidence_obtained",
    "review_status", "partial_rate", "prompt_tokens", "completion_tokens",
    "latency_s", "findings_total", "findings_source_verified",
    "findings_correct", "findings_false_positive",
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def build_case(root: Path, case: dict) -> Path:
    repo = root / case["id"]
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "e@example.com")
    _git(repo, "config", "user.name", "e")
    for rel, body in BASE_FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", case["branch"])
    (repo / case["head_file"]).write_text(HEAD_FILES[case["head_body"]], encoding="utf-8")
    _git(repo, "commit", "-qam", f"case {case['id']}")
    _git(repo, "checkout", "-q", "main")
    return repo


def freeze(payload_out: Path, markdown_out: Path) -> dict:
    payload = {
        "frozen": True,
        "budgets": {"max_tool_calls": MAX_TOOL_CALLS, "max_tool_rounds": MAX_ROUNDS,
                    "search_top_k": TOP_K},
        "metrics": METRICS,
        "cases": [
            {
                "id": case["id"], "label": case["label"], "branch": case["branch"],
                "head_file": case["head_file"],
                "gold": case["gold"], "required_evidence": case["required_evidence"],
                "rationale": case["why"],
            }
            for case in CASES
        ],
        "note": ("Defined before any optimisation. Gold is a (file, qualname, line) "
                 "triple; required_evidence lists the ranges a reviewer must actually "
                 "receive to judge the case. No model run backs these cases yet."),
    }
    payload_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    markdown_out.write_text(render(payload), encoding="utf-8")
    return payload


def render(payload: dict) -> str:
    b = payload["budgets"]
    lines = [
        "# V4.3 frozen dev evaluation",
        "",
        "Six independent cases, fixed **before** any efficiency work: three cross-file",
        "bugs and three clean PRs. No case overlaps the frozen held-out sets or the",
        "historical smoke repositories.",
        "",
        (f"Budgets: `max_tool_calls={b['max_tool_calls']}`, "
        f"`max_tool_rounds={b['max_tool_rounds']}`, `search_top_k={b['search_top_k']}`."),
        "",
        "## Cases",
        "",
        "| id | label | branch | head file | gold | required evidence |",
        "|---|---|---|---|---|---|",
    ]
    for case in payload["cases"]:
        gold = case["gold"]
        lines.append(f"| {case['id']} | {case['label']} | `{case['branch']}` | "
                     f"`{case['head_file']}` | `{gold['file_path']}:{gold['line']}` "
                     f"`{gold['qualname']}` | {', '.join('`' + r + '`' for r in case['required_evidence'])} |")
    lines += ["", "## Rationale", ""]
    for case in payload["cases"]:
        lines.append(f"* **{case['id']}** — {case['rationale']}")
    lines += ["", "## Metrics", "",
              "| metric | meaning |", "|---|---|",
              "| `tool_calls_requested` | calls the model asked for |",
              "| `tool_calls_executed` | calls that really ran |",
              "| `tool_calls_denied` | refused by the budget |",
              "| `invalid_argument_calls` | validation failures (category C) |",
              "| `empty_searches` | searches returning nothing (D) |",
              "| `exact_repeat_calls` | identical executed calls (A) |",
              "| `overlapping_read_calls` | different arguments, overlapping lines (B) |",
              "| `required_evidence_obtained` | how many required ranges were delivered |",
              "| `review_status` / `partial_rate` | process outcome |",
              "| `prompt_tokens`, `completion_tokens`, `latency_s` | cost |",
              "| `findings_*`, `findings_correct`, `findings_false_positive` | quality |",
              "",
              "## Acceptance rule",
              "",
              "A change is kept only if it **keeps `required_evidence_obtained` and",
              "`findings_correct` unchanged** while reducing executed calls or denials.",
              "Fewer calls with missing evidence is a regression, not a win.",
              "",
              "## Status",
              "",
              "The cases are frozen; no model has been run against them yet. Measuring",
              "`findings_correct` requires a live run, which this round does not do.",
              ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("docs/v4.3-dev-eval"))
    args = ap.parse_args()
    if not args.freeze:
        print("nothing to do; pass --freeze")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    payload = freeze(args.out / "cases.json", args.out / "CASES.md")
    print(json.dumps({"cases": len(payload["cases"]),
                      "budgets": payload["budgets"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
