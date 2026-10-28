"""Deterministic checks for the V4.3 audit and the frozen dev cases.

No model calls: the classifier is pure, and the cases are built from committed
sources.
"""

import json
import subprocess
from pathlib import Path

import pytest

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.git_reader import read_changed_files
from scripts.budget_eval_cases import CASES, build_case
from scripts.trace_audit import classify


def _call(name, args, outcome="hit", round_no=1, call_id="c", code=""):
    return {"name": name, "arguments": args, "outcome": outcome,
            "round": round_no, "call_id": call_id, "error_code": code}


def test_classifier_labels_exact_repeats():
    run = {"case": "x", "tool_calls": [
        _call("read_code_context", {"file_path": "src/a.py", "start_line": 1, "end_line": 10}),
        _call("read_code_context", {"file_path": "src/a.py", "start_line": 1, "end_line": 10}),
    ]}
    rows = classify([("src", "x", run)])
    assert [r["category"] for r in rows] == ["F", "A"]
    assert "identical to call #1" in rows[1]["why"]


def test_classifier_labels_overlapping_reads_as_b_not_a():
    run = {"case": "x", "tool_calls": [
        _call("read_code_context", {"file_path": "src/a.py", "start_line": 1, "end_line": 10}),
        _call("read_code_context", {"file_path": "src/a.py", "start_line": 5, "end_line": 12}),
    ]}
    rows = classify([("src", "x", run)])
    assert [r["category"] for r in rows] == ["F", "B"]


def test_classifier_keeps_errors_misses_and_denials_apart():
    run = {"case": "x", "tool_calls": [
        _call("read_code_context", {"file_path": "tests/x.py"}, outcome="error", code="denied_path"),
        _call("search_code", {"query": "zzz"}, outcome="miss"),
        _call("search_code", {"query": "zzz"}, outcome="budget_denied"),
    ]}
    rows = classify([("src", "x", run)])
    assert [r["category"] for r in rows] == ["C", "D", "E"]
    # a denied call never counts as an executed duplicate
    assert rows[2]["why"] == "refused before execution"


def test_search_then_read_is_not_a_duplicate():
    """A snippet is not proof of a read, so the follow-up read is new evidence."""
    run = {"case": "x", "tool_calls": [
        _call("search_code", {"query": "estimate"}),
        _call("read_code_context", {"file_path": "src/shipping.py", "start_line": 1, "end_line": 8}),
    ]}
    rows = classify([("src", "x", run)])
    assert [r["category"] for r in rows] == ["F", "F"]


def test_committed_traces_have_no_exact_repeats():
    """The measurement that drives the No-Go decision, pinned as a test."""
    sources = ["docs/v4-evidence-smoke/results.json", "docs/rag-smoke/results.json",
               "docs/mcp-smoke/results.json"]
    runs = []
    for rel in sources:
        path = Path(rel)
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for run in data.get("runs") or data.get("cases") or []:
            runs.append((rel, run.get("case", "?"), run))
    if not runs:
        pytest.skip("no recorded traces present")
    rows = classify(runs)
    assert rows, "the audit found no calls"
    assert sum(1 for r in rows if r["category"] == "A") == 0


# ---- frozen dev cases ------------------------------------------------------

def test_six_cases_build_with_reachable_gold(tmp_path):
    assert len(CASES) == 6
    assert sum(1 for c in CASES if c["label"] == "bug") == 3
    assert sum(1 for c in CASES if c["label"] == "clean") == 3
    for case in CASES:
        repo = build_case(tmp_path / case["id"], case)
        files, diff = read_changed_files(repo, "main", case["branch"])
        assert [f.file_path for f in files] == [case["head_file"]]
        assert diff.strip()
        codebase = TrustedCodebase.from_git(repo, case["branch"])
        gold = case["gold"]
        payload = codebase.read_code_context(gold["file_path"],
                                             max(1, gold["line"] - 2), gold["line"] + 2)
        assert gold["qualname"] in payload["content"], case["id"]
        assert payload["ref"] == codebase.snapshot_sha


def test_frozen_cases_file_matches_the_source(tmp_path):
    """The committed definition must not drift from the generator."""
    frozen = Path("docs/v4.3-dev-eval/cases.json")
    if not frozen.is_file():
        pytest.skip("cases not frozen yet")
    payload = json.loads(frozen.read_text(encoding="utf-8"))
    assert payload["frozen"] is True
    assert [c["id"] for c in payload["cases"]] == [c["id"] for c in CASES]
    for entry, case in zip(payload["cases"], CASES, strict=True):
        assert entry["gold"] == case["gold"]
        assert entry["required_evidence"] == case["required_evidence"]
        assert entry["label"] == case["label"]
    assert payload["budgets"]["max_tool_calls"] == 4


def test_audit_report_exists_and_reports_zero_repeats():
    report = Path("docs/v4.3-trace-audit.md")
    if not report.is_file():
        pytest.skip("audit not generated")
    text = report.read_text(encoding="utf-8")
    assert "| A | 0 |" in text
    assert "No exact repeats exist" in text


def _git(repo: Path, *args: str) -> str:  # pragma: no cover - helper shape
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout
