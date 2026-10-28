"""Frozen V4.4 dev cases: three cross-file bugs and three clean PRs.

Six independent repositories, built deterministically (fixed commit dates) so the
SHAs are reproducible. Each case carries:

* a head branch with the change, diffed against `main`;
* a **hidden test** (`tests/test_gold.py`) that fails on the bug branch and passes
  on `main` and on the clean branch — the label is executable, not asserted;
* the gold location `(file, qualname, line)`;
* the evidence ranges a reviewer must actually obtain to judge the case.

The hidden test lives under `tests/`, which the tool allowlist refuses to read, so
the agent cannot see the answer.

Run:
    python scripts/budget_eval_cases.py --freeze     # write the definition
    python scripts/budget_eval_cases.py --verify     # build and run hidden tests
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_TOOL_CALLS = 4
MAX_ROUNDS = 3
TOP_K = 5
FIXED_DATE = "2026-01-01T00:00:00+0000"

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
    "src/checkout.py": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg), 2)


def place(available, requested):
    """Place an order, reporting a shortage as a validation failure."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0
''',
}

HEAD_FILES = {
    "bug_cents": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(to_cents(weight_kg)), 2)


def place(available, requested):
    """Place an order, reporting a shortage as a validation failure."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0
''',
    "bug_kwarg": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg, express=True), 2)


def place(available, requested):
    """Place an order, reporting a shortage as a validation failure."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0
''',
    # the callee changes its exception type; the caller still catches ValueError
    "bug_exception": '''"""Stock."""


def reserve(available, requested):
    """Reserve stock, raising when there is not enough."""
    if requested > available:
        raise LookupError("insufficient stock")
    return available - requested
''',
    "clean_kwarg": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg), 2)


def place(available, requested):
    """Place an order, reporting a shortage as a validation failure."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0


def label(weight_kg, net):
    """Return a short label for the order."""
    return "order " + str(total(weight_kg, net))
''',
    "clean_identity": '''"""Checkout."""

from .money import to_cents
from .shipping import estimate
from .stock import reserve


def total(weight_kg, net):
    """Return the order total."""
    return round(net + estimate(weight_kg), 2)


def place(available, requested):
    """Place an order, reporting a shortage as a validation failure."""
    try:
        return reserve(available, requested)
    except ValueError:
        return 0


def dollars_as_cents(dollars):
    """Return the same amount expressed in cents."""
    return to_cents(dollars)
''',
    # behaviour-preserving rewrite: same result for every input
    "clean_refactor": '''"""Stock."""


def reserve(available, requested):
    """Reserve stock, raising when there is not enough."""
    if requested > available:
        raise ValueError("insufficient stock")
    return available - requested + 0
''',
}

# hidden tests encode main's behaviour: a bug branch fails, a clean branch passes.
# The agent cannot read them: `tests/` is outside the tool allowlist.
HIDDEN_TESTS = {
    "B1-cents": '''from src.checkout import total


def test_total_uses_shipping_units():
    assert total(2.0, 10.0) == 17.0
''',
    "B2-kwarg": '''from src.checkout import total


def test_total_does_not_raise():
    assert total(2.0, 10.0) == 17.0
''',
    "B3-exception": '''from src.checkout import place


def test_shortage_is_reported_not_raised():
    assert place(5, 10) == 0
    assert place(5, 2) == 3
''',
    "C1-kwarg": '''from src.checkout import label, total


def test_order_label():
    assert total(2.0, 10.0) == 17.0
    assert label(2.0, 10.0) == "order 17.0"
''',
    "C2-identity": '''from src.checkout import dollars_as_cents


def test_identity_conversion():
    assert dollars_as_cents(0) == 0
    assert dollars_as_cents(2.5) == 250
''',
    "C3-refactor": '''from src.stock import reserve


def test_reserve_behaviour_unchanged():
    assert reserve(5, 2) == 3
    assert reserve(5, 0) == 5
    try:
        reserve(5, 10)
    except ValueError:
        pass
    else:
        raise AssertionError("shortage must raise")
''',
}

CASES = [
    {
        "id": "B1-cents", "label": "bug", "branch": "case/b1",
        "head_file": "src/checkout.py", "head_body": "bug_cents",
        "gold": {"file_path": "src/shipping.py", "qualname": "estimate", "line": 4},
        "required_evidence": ["src/shipping.py:1-8", "src/checkout.py:1-16"],
        "why": "a cents value reaches a dollars parameter; only the callee's "
               "docstring decides it",
    },
    {
        "id": "B2-kwarg", "label": "bug", "branch": "case/b2",
        "head_file": "src/checkout.py", "head_body": "bug_kwarg",
        "gold": {"file_path": "src/shipping.py", "qualname": "estimate", "line": 4},
        "required_evidence": ["src/shipping.py:1-8"],
        "why": "estimate() takes no express keyword, so the call raises at runtime",
    },
    {
        "id": "B3-exception", "label": "bug", "branch": "case/b3",
        "head_file": "src/stock.py", "head_body": "bug_exception",
        "gold": {"file_path": "src/stock.py", "qualname": "reserve", "line": 4},
        "required_evidence": ["src/stock.py:1-8", "src/checkout.py:14-20"],
        "why": "the callee raises a new exception type and the caller only catches "
               "ValueError; both sides are needed to see it",
    },
    {
        "id": "C1-kwarg", "label": "clean", "branch": "case/c1",
        "head_file": "src/checkout.py", "head_body": "clean_kwarg",
        "gold": {"file_path": "src/checkout.py", "qualname": "label", "line": 19},
        "required_evidence": ["src/checkout.py:18-21", "src/checkout.py:1-16"],
        "why": "a new caller is fine, but the reviewer must read it and the callee",
    },
    {
        "id": "C2-identity", "label": "clean", "branch": "case/c2",
        "head_file": "src/checkout.py", "head_body": "clean_identity",
        "gold": {"file_path": "src/checkout.py", "qualname": "dollars_as_cents",
                 "line": 19},
        "required_evidence": ["src/checkout.py:18-21", "src/money.py:1-6"],
        "why": "a conversion looks risky until the callee confirms it is exact",
    },
    {
        "id": "C3-refactor", "label": "clean", "branch": "case/c3",
        "head_file": "src/stock.py", "head_body": "clean_refactor",
        "gold": {"file_path": "src/stock.py", "qualname": "reserve", "line": 4},
        "required_evidence": ["src/stock.py:1-8"],
        "why": "a rewrite that preserves the result for every input",
    },
]

METRICS = [
    "model_requests", "model_failures",
    "tool_calls_requested", "tool_calls_executed", "tool_calls_denied",
    "tool_errors", "tool_misses",
    "required_evidence_obtained", "evidence_required_total",
    "review_status", "findings_total", "findings_source_verified",
    "finding_correct", "finding_false_positive",
    "prompt_tokens", "completion_tokens", "latency_s",
]


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE,
               GIT_AUTHOR_NAME="eval", GIT_AUTHOR_EMAIL="eval@example.com",
               GIT_COMMITTER_NAME="eval", GIT_COMMITTER_EMAIL="eval@example.com")
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def build_case(root: Path, case: dict) -> Path:
    repo = root / case["id"]
    if repo.exists():
        return repo
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "eval@example.com")
    _git(repo, "config", "user.name", "eval")
    for rel, body in BASE_FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (repo / "tests" / "test_gold.py").write_text(HIDDEN_TESTS[case["id"]], encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", case["branch"])
    (repo / case["head_file"]).write_text(HEAD_FILES[case["head_body"]], encoding="utf-8")
    _git(repo, "commit", "-qam", f"case {case['id']}")
    _git(repo, "checkout", "-q", "main")
    return repo


def run_hidden_tests(repo: Path, ref: str) -> bool:
    work = repo.parent / f"_check_{repo.name}_{ref.replace('/', '_')}"
    if work.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force",
                        str(work)], check=False, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach",
                    str(work), ref], check=True, capture_output=True, text=True)
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(work)
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q",
                               "-p", "no:cacheprovider", "tests/test_gold.py"],
                              cwd=work, capture_output=True, text=True, check=False,
                              env=env)
        return proc.returncode == 0
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force",
                        str(work)], check=False, capture_output=True, text=True)


def verify(root: Path) -> dict:
    report = {}
    for case in CASES:
        repo = build_case(root, case)
        main_ok = run_hidden_tests(repo, "main")
        head_ok = run_hidden_tests(repo, case["branch"])
        expected_clean = case["label"] == "clean"
        report[case["id"]] = {
            "main_passes": main_ok,
            "head_passes": head_ok,
            "label": case["label"],
            "label_matches": head_ok == expected_clean,
            "sha_main": _git(repo, "rev-parse", "main").strip(),
            "sha_head": _git(repo, "rev-parse", case["branch"]).strip(),
        }
    return report


def _required_evidence(repo: Path, case: dict) -> list[str]:
    """Required ranges are derived from the code, never hand-written.

    The gold function's span is mandatory. When the callee lives in a different
    file from the change, the changed file is mandatory too: the defect is only
    visible by reading both sides.
    """
    from pr_guardian.agent_tools import TrustedCodebase
    from pr_guardian.repo_index import build_index

    codebase = TrustedCodebase.from_git(repo, case["branch"])
    index = build_index(codebase)
    gold = case["gold"]
    spans: list[str] = []
    for chunk in index.chunks:
        if chunk.file_path == gold["file_path"] and chunk.qualname == gold["qualname"]:
            spans.append(f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}")
            break
    if not spans:
        raise SystemExit(f"gold not found for {case['id']}")
    callers = [c for c in index.chunks
               if c.file_path != gold["file_path"]
               and gold["qualname"] in (c.calls or [])]
    if callers:
        caller = min(callers, key=lambda c: (c.file_path, c.start_line))
        spans.append(f"{caller.file_path}:{caller.start_line}-{caller.end_line}")
    return spans


def freeze(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "frozen": True,
        "budgets": {"max_tool_calls": MAX_TOOL_CALLS, "max_tool_rounds": MAX_ROUNDS,
                    "search_top_k": TOP_K},
        "metrics": METRICS,
        "cases": [],
        "note": ("Built with fixed commit dates so the SHAs are reproducible. Labels "
                 "are verified by tests/test_gold.py: it passes on main and on clean "
                 "branches and fails on bug branches. The agent cannot read tests/."),
    }
    for case in CASES:
        repo = build_case(root, case)
        payload["cases"].append({
            "id": case["id"], "label": case["label"], "branch": case["branch"],
            "head_file": case["head_file"],
            "gold": case["gold"],
            "required_evidence": _required_evidence(repo, case),
            "rationale": case["why"],
            "sha_main": _git(repo, "rev-parse", "main").strip(),
            "sha_head": _git(repo, "rev-parse", case["branch"]).strip(),
        })
    (out / "cases.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    (out / "CASES.md").write_text(render(payload), encoding="utf-8")
    return payload


def render(payload: dict) -> str:
    b = payload["budgets"]
    lines = [
        "# V4.4 frozen dev cases",
        "",
        "Six independent repositories, built deterministically (fixed commit dates).",
        "Every label is executable: `tests/test_gold.py` passes on `main` and on the",
        "clean branch, and fails on the bug branch. `tests/` is outside the tool",
        "allowlist, so the agent cannot read the answer.",
        "",
        (f"Budgets: `max_tool_calls={b['max_tool_calls']}`, "
        f"`max_tool_rounds={b['max_tool_rounds']}`, `search_top_k={b['search_top_k']}`."),
        "",
        "| id | label | head sha | gold | required evidence |",
        "|---|---|---|---|---|",
    ]
    for case in payload["cases"]:
        g = case["gold"]
        lines.append(f"| {case['id']} | {case['label']} | `{case['sha_head'][:12]}` | "
                     f"`{g['file_path']}:{g['line']}` `{g['qualname']}` | "
                     f"{', '.join('`' + r + '`' for r in case['required_evidence'])} |")
    lines += ["", "## Rationale", ""]
    for case in payload["cases"]:
        lines.append(f"* **{case['id']}** — {case['rationale']}")
    lines += ["", "## Metrics", ", ".join(f"`{m}`" for m in payload["metrics"]), ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("docs/v4.4-dev-eval"))
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="pr-guardian-v44-"))
    if args.verify:
        print(json.dumps(verify(root), indent=2))
        return 0
    if args.freeze:
        payload = freeze(root, args.out)
        print(json.dumps({"cases": len(payload["cases"]),
                          "shas": {c["id"]: c["sha_head"][:12]
                                   for c in payload["cases"]}}, indent=2))
        return 0
    print("nothing to do; pass --freeze or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
