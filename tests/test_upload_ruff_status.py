"""Ruff outcome handling: a failed lint must never look like a clean run.

The analyser reports `ok | empty | timeout | unavailable | exit_error | unparsable |
unexpected`. Only `ok` (with an empty list) and `empty` mean "Ruff ran and found
nothing"; every other value keeps the report `partial`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pr_guardian import upload_worker
from pr_guardian.settings import Settings
from pr_guardian.upload_review import run_upload_review
from pr_guardian.uploads import collect_from_files

GOOD = b"def add(a, b):\n    return a + b\n"


def settings() -> Settings:
    return Settings(api_key="", base_url="https://example.test", model="m",
                    public_mode=False, allow_live_llm=False,
                    allow_test_execution=False, max_live_llm_calls=1,
                    upload_enabled=True)


class FakeResult:
    def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, result=None, exc=None):
    def fake(*args, **kwargs):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(upload_worker.subprocess, "run", fake)


def _work(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    return tmp_path


def test_ok_with_findings(tmp_path, monkeypatch):
    payload = [{"code": "F401", "message": "unused",
                "location": {"row": 1}, "end_location": {"row": 1},
                "filename": str(tmp_path / "a.py")}]
    _patch_run(monkeypatch, FakeResult(0, json.dumps(payload)))
    status, items = upload_worker._ruff(tmp_path)
    assert status == "ok"
    assert len(items) == 1


def test_ok_with_no_findings_is_empty_list_not_failure(tmp_path, monkeypatch):
    _patch_run(monkeypatch, FakeResult(0, "[]"))
    status, items = upload_worker._ruff(tmp_path)
    assert status == "ok"
    assert items == []


def test_silent_output_is_reported_as_empty(tmp_path, monkeypatch):
    """`ruff --output-format json` writes `[]` for no findings; empty stdout is not that."""
    _patch_run(monkeypatch, FakeResult(0, "   "))
    assert upload_worker._ruff(tmp_path) == ("empty", [])


def test_empty_stdout_is_not_a_completed_review(monkeypatch):
    payload = {
        "files": [{"path": "a.py", "lines": 2, "syntax_error": ""}],
        "findings": [],
        "ruff_status": "empty",
        "ast_status": "ok",
        "files_parsed": 1,
        "files_with_syntax_errors": 0,
    }
    monkeypatch.setattr("pr_guardian.upload_review._run_worker",
                        lambda root: (payload, "ok"))
    report = run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert report.upload_analysis.ruff_status == "empty"
    assert report.review_status == "partial"
    assert report.completeness.findings_signal == "not_evaluated"
    assert any("no output at all" in note for note in report.notes)


def test_timeout_is_reported(tmp_path, monkeypatch):
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="ruff", timeout=1))
    assert upload_worker._ruff(tmp_path) == ("timeout", [])


def test_missing_tool_is_reported(tmp_path, monkeypatch):
    _patch_run(monkeypatch, exc=FileNotFoundError("ruff"))
    assert upload_worker._ruff(tmp_path) == ("unavailable", [])


def test_nonzero_exit_is_reported_not_swallowed(tmp_path, monkeypatch):
    _patch_run(monkeypatch, FakeResult(2, "", "ruff crashed"))
    assert upload_worker._ruff(tmp_path) == ("exit_error", [])


def test_unparsable_output_is_reported(tmp_path, monkeypatch):
    _patch_run(monkeypatch, FakeResult(0, "not json at all"))
    assert upload_worker._ruff(tmp_path) == ("unparsable", [])


def test_unexpected_json_shape_is_reported(tmp_path, monkeypatch):
    _patch_run(monkeypatch, FakeResult(0, '{"ok": true}'))
    assert upload_worker._ruff(tmp_path) == ("unexpected", [])


@pytest.mark.parametrize("status", ["empty", "timeout", "unavailable", "exit_error",
                                    "unparsable", "unexpected", "error"])
def test_every_ruff_failure_keeps_findings_and_marks_partial(monkeypatch, status):
    payload = {
        "files": [{"path": "a.py", "lines": 2, "syntax_error": ""}],
        "findings": [{"file_path": "a.py", "start_line": 1, "end_line": 1,
                      "rule": "UP001", "severity": "high", "category": "heuristic",
                      "summary": "eval", "explanation": "", "suggested_fix": "",
                      "source": "heuristic"}],
        "ruff_status": status,
        "ast_status": "ok",
        "files_parsed": 1,
        "files_with_syntax_errors": 0,
    }
    monkeypatch.setattr("pr_guardian.upload_review._run_worker",
                        lambda root: (payload, "ok"))
    report = run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert report.upload_analysis.ruff_status == status
    assert report.review_status == "partial"
    assert report.completeness.reason == "partial_analysis"
    assert [f.rule for f in report.final_findings] == ["UP001"]
    expected_note = ("no output at all" if status == "empty"
                     else "Ruff could not complete")
    assert any(expected_note in note for note in report.notes)


def test_ruff_ok_with_no_findings_can_be_completed():
    report = run_upload_review(collect_from_files([("clean.py", GOOD)]), settings())
    assert report.upload_analysis.ruff_status in {"ok", "empty"}
    assert report.review_status == "completed"
    assert any("does not prove" in note for note in report.notes)


def test_worker_reports_the_real_ruff_status_end_to_end(tmp_path):
    (tmp_path / "clean.py").write_text("def add(a, b):\n    return a + b\n",
                                       encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", "-m", "pr_guardian.upload_worker",
                           str(tmp_path)], capture_output=True, text=True, timeout=60,
                          check=False)
    payload = json.loads(proc.stdout)
    assert payload["ruff_status"] == "ok"
    assert payload["ast_status"] == "ok"
