"""Review completeness: process state derived from execution records.

No model calls. Every case drives pipeline.run_review with a stubbed reviewer whose
trace is the source of truth for the status.
"""

import json

import pytest

from pr_guardian import pipeline
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.models import (
    EvidenceRef,
    FindingSource,
    ReviewFinding,
    VerificationStatus,
)
from pr_guardian.settings import Settings
from pr_guardian.tool_calling import ToolCallRecord, ToolReviewResult

DEMO_BRANCH = "feature/boundary-bug"


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "deepseek-flash",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": False,
        "max_live_llm_calls": 50, "tool_calls_enabled": True,
        "max_tool_rounds": 3, "max_tool_calls": 4,
    }
    base.update(over)
    return Settings(**base)


def _call(round_no: int, name: str, args: dict, outcome: str, call_id: str) -> ToolCallRecord:
    return ToolCallRecord(round_no, name, args, outcome == "hit", outcome,
                          "" if outcome == "hit" else outcome, "", 100, 0.01, 0, call_id)


def _finding(codebase, path="src/discount.py", start=6, end=6, cite=(4, 4)):
    """A finding anchored on the changed line 6, citing a separate range."""
    refs = []
    if cite:
        refs = [EvidenceRef(ref=codebase.snapshot_sha, file_path=path,
                            start_line=cite[0], end_line=cite[1],
                            tool_call_id="call_1", tool_name="read_code_context")]
    return ReviewFinding(
        id="llm-1", file_path=path, start_line=start, end_line=end,
        category="logic", severity="high", summary="s", source=FindingSource.LLM,
        verification_status=VerificationStatus.SUSPECTED, evidence_refs=refs,
    )


def _result(codebase, status, findings, trace, schema_valid=True, calls=1,
            failures=0, usage=None, error="", registry=None):
    return ToolReviewResult(
        findings=findings, notes=["PARTIAL"] if status == "tool_limit" else [],
        schema_valid=schema_valid, model="deepseek-flash",
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 10},
        trace=trace, rounds=len({c.round_index for c in trace}) or 1,
        model_attempts=calls, model_successes=calls - failures,
        model_failures=failures, status=status, error=error, registry=registry,
    )


def _registry_with_read(codebase):
    payload = codebase.read_code_context("src/discount.py", 1, 6)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_call(1, "read_code_context",
                          {"file_path": "src/discount.py", "start_line": 1,
                           "end_line": 6}, "hit", "call_1"), payload)
    return registry


def _run(demo_repo, monkeypatch, result=None, use_llm=True, **settings):
    if result is not None:
        monkeypatch.setattr(pipeline, "review_with_tools", lambda *a, **k: result)
    return pipeline.run_review(demo_repo, "main", DEMO_BRANCH, use_llm=use_llm,
                               run_tests=False, settings=_settings(**settings))


# ---- completed -------------------------------------------------------------

def test_completed_with_findings(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "ok", [_finding(codebase)], trace,
                          registry=_registry_with_read(codebase)))
    assert report.review_status == "completed"
    assert report.completeness.status == "completed"
    assert report.completeness.findings_signal == "has_findings"
    assert report.completeness.model_requests == 1
    assert report.source_verified_findings


def test_completed_with_zero_findings_is_not_proof_of_correctness(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "ok", [], trace,
                          registry=_registry_with_read(codebase)))
    assert report.review_status == "completed"
    # explicitly "no findings in a completed review", never "clean/confirmed"
    assert report.completeness.findings_signal == "no_findings_in_completed_review"
    assert report.final_findings == []


# ---- partial ---------------------------------------------------------------

def test_partial_with_findings(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [
        _call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1"),
        _call(2, "read_code_context", {"file_path": "src/orders.py"}, "budget_denied", ""),
    ]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "tool_limit", [_finding(codebase)], trace,
                          registry=_registry_with_read(codebase)))
    assert report.review_status == "partial"
    assert report.completeness.reason == "tool_budget_exhausted"
    assert report.completeness.tool_calls_denied == 1
    assert report.completeness.denied_requests[0]["arguments"]["file_path"] == "src/orders.py"
    assert report.completeness.findings_signal == "has_findings"
    assert report.final_findings, "a partial review keeps what it found"


def test_partial_without_findings_is_never_a_clean_review(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1"),
             _call(2, "read_code_context", {"file_path": "src/x.py"}, "budget_denied", "")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "tool_limit", [], trace,
                          registry=_registry_with_read(codebase)))
    assert report.review_status == "partial"
    assert report.completeness.findings_signal == "not_evaluated"
    assert report.final_findings == []


def test_duplicate_reads_are_counted(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    same = {"file_path": "src/discount.py", "start_line": 1, "end_line": 6}
    trace = [_call(1, "read_code_context", same, "hit", "a"),
             _call(1, "read_code_context", same, "hit", "b"),
             _call(2, "read_code_context", same, "hit", "c")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "ok", [], trace,
                          registry=_registry_with_read(codebase)))
    assert report.completeness.duplicate_calls == 2


def test_tool_errors_and_model_failures_are_counted_separately(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "tests/x.py"}, "error", "a"),
             _call(1, "find_symbol_definition", {"symbol": "zzz"}, "miss", "b")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "schema_invalid", [], trace, schema_valid=False,
                          calls=3, failures=1, error="schema_invalid"))
    assert report.review_status == "failed"
    assert report.completeness.tool_errors == 1
    assert report.completeness.tool_misses == 1
    assert report.completeness.model_failures == 1
    assert report.completeness.model_requests == 3


# ---- not_run / failed ------------------------------------------------------

@pytest.mark.parametrize("kwargs,use_llm", [
    ({"api_key": ""}, True),
    ({"public_mode": True}, True),
    ({}, False),
])
def test_not_run_states_do_not_invent_a_model(demo_repo, monkeypatch, kwargs, use_llm):
    if kwargs == {"api_key": ""}:
        result = ToolReviewResult([], ["no key"], False, model="deepseek-flash",
                                  status="not_configured", error="no_api_key")
    elif kwargs == {"public_mode": True}:
        result = ToolReviewResult([], ["policy"], False, model="deepseek-flash",
                                  status="disabled", error="disabled_by_policy")
    else:
        result = None
    report = _run(demo_repo, monkeypatch, result, use_llm=use_llm, **kwargs)
    assert report.review_status == "not_run"
    assert report.completeness.model_requests == 0
    assert report.llm_prompt_tokens == 0
    assert report.model_name == "none"
    assert report.evidence_sources == []
    assert report.completeness.findings_signal in {"not_evaluated", "has_findings"}


def test_model_error_is_failed(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "model_error", [], [], calls=1, failures=1,
                          error="http:ReadTimeout"))
    assert report.review_status == "failed"
    assert report.completeness.reason


# ---- findings grouping -----------------------------------------------------

def test_verified_finding_is_grouped_but_never_confirmed(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "ok", [_finding(codebase)], trace,
                          registry=_registry_with_read(codebase)))
    assert [f.id for f in report.source_verified_findings] == ["llm-1"]
    verified = report.source_verified_findings[0]
    assert verified.claim_status == "claim_unjudged"
    assert verified.verification_status == VerificationStatus.SUSPECTED
    assert report.source_unverified_findings == []


def test_unverified_finding_keeps_its_own_group(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, DEMO_BRANCH)
    trace = [_call(1, "read_code_context", {"file_path": "src/discount.py"}, "hit", "call_1")]
    report = _run(demo_repo, monkeypatch,
                  _result(codebase, "ok", [_finding(codebase, cite=(7, 8))], trace,
                          registry=_registry_with_read(codebase)))
    assert [f.id for f in report.source_unverified_findings] == ["llm-1"]
    assert report.source_unverified_findings[0].evidence_status == "source_unverified"
    assert report.source_verified_findings == []
    # still visible: provenance failure never deletes a finding
    assert [f.id for f in report.final_findings] == ["llm-1"]


def test_ruff_findings_are_deterministic_not_llm_evidence():
    from pr_guardian.evidence import verify_finding

    ruff = ReviewFinding(
        id="ruff-1", file_path="src/discount.py", start_line=6, end_line=6,
        category="boundary-condition", severity="low", summary="ruff", rule="E501",
        source=FindingSource.RUFF, verification_status=VerificationStatus.CONFIRMED)
    checked = verify_finding(ruff, None, "0" * 40, set())
    assert checked.evidence_status == "deterministic"
    assert checked.claim_status == "deterministic"
    assert checked.verification_status == VerificationStatus.CONFIRMED


def test_old_report_still_parses_with_the_new_fields():
    from pr_guardian.models import ReviewReport

    legacy = {"review_id": "x", "repository": "d", "base_ref": "m", "head_ref": "h",
              "model_name": "none", "created_at": "2026-01-01T00:00:00Z"}
    report = ReviewReport.model_validate(legacy)
    assert report.review_status == "completed"
    assert report.completeness.status == "completed"
    assert json.loads(report.model_dump_json())["completeness"]["findings_signal"] \
        == "not_evaluated"
