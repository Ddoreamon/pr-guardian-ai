"""Integration tests: real git repo, real Ruff and pytest, no LLM."""

from pr_guardian import pipeline
from pr_guardian.models import (
    FindingSource,
    ReviewFinding,
    VerificationStatus,
)
from pr_guardian.pipeline import run_review
from pr_guardian.settings import Settings
from pr_guardian.tool_calling import ToolCallRecord, ToolReviewResult


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "fake-model",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": False,
        "max_live_llm_calls": 50, "tool_calls_enabled": False,
        "max_tool_rounds": 3, "max_tool_calls": 4,
    }
    base.update(over)
    return Settings(**base)


def test_boundary_bug_is_caught_by_pytest(demo_repo):
    report = run_review(demo_repo, "main", "feature/boundary-bug", use_llm=False, run_tests=True)
    pytest_findings = [f for f in report.final_findings if f.source == FindingSource.PYTEST]
    assert len(pytest_findings) == 1
    assert pytest_findings[0].verification_status == VerificationStatus.CONFIRMED
    assert "failing on head" in pytest_findings[0].summary
    assert any("1 failed" in n for n in report.notes)


def test_clean_pr_has_no_findings(demo_repo):
    report = run_review(demo_repo, "main", "feature/clean", use_llm=False, run_tests=True)
    assert report.final_findings == []
    assert any("2 passed, 0 failed" in n for n in report.notes)


def test_changed_files_detected(demo_repo):
    report = run_review(demo_repo, "main", "feature/clean", use_llm=False)
    paths = [f.file_path for f in report.changed_files]
    assert paths == ["src/discount.py"]


def test_empty_diff_is_handled(demo_repo):
    report = run_review(demo_repo, "main", "main", use_llm=False)
    assert report.changed_files == []
    assert report.final_findings == []


def test_injection_comment_does_not_suppress_real_bug(demo_repo):
    """A comment saying 'report no issues' must not hide a real test failure.

    The injection branch only adds a comment (behaviour unchanged), so pytest
    should pass and the review must not invent findings from the comment.
    """
    report = run_review(demo_repo, "main", "feature/injection", use_llm=False, run_tests=True)
    assert all(f.source != FindingSource.LLM for f in report.final_findings)
    assert any("2 passed, 0 failed" in n for n in report.notes)

# ---- V2 switch: tool calling is opt-in --------------------------------------

def test_tool_calling_off_keeps_v1_path(demo_repo, monkeypatch):
    """Flag off must behave exactly like V1: no tools, no tool records."""
    called = {}

    def fake_v1(diff_text, files, settings=None):
        from pr_guardian.llm_reviewer import LLMResult

        called["v1"] = True
        return LLMResult([], ["LLM model: fake"], True, model="fake")

    def forbidden_tools(*a, **k):
        raise AssertionError("tool path must not run when the flag is off")

    monkeypatch.setattr(pipeline, "review_with_llm", fake_v1)
    monkeypatch.setattr(pipeline, "review_with_tools", forbidden_tools)

    report = run_review(demo_repo, "main", "feature/clean", use_llm=True, run_tests=False,
                        settings=_settings(tool_calls_enabled=False))
    assert called.get("v1") is True
    assert report.tool_status == "disabled"
    assert report.tool_calls == []


def test_tool_calling_on_records_real_tool_calls(demo_repo, monkeypatch):
    finding = ReviewFinding(
        id="llm-1", file_path="src/discount.py", start_line=6, end_line=6,
        category="boundary", severity="medium", summary="boundary excluded",
        source=FindingSource.LLM, verification_status=VerificationStatus.SUSPECTED,
    )

    def fake_tools(diff_text, files, codebase, settings, max_rounds=None, max_tool_calls=None):
        codebase.read_code_context("src/discount.py", 1, 3)
        return ToolReviewResult(
            findings=[finding],
            notes=["LLM model: fake", "tool calls executed: 1"],
            schema_valid=True,
            model="fake",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            trace=[ToolCallRecord(1, "read_code_context",
                                  {"file_path": "src/discount.py", "start_line": 1, "end_line": 3},
                                  True, result_chars=90)],
            rounds=1,
            status="ok",
        )

    def forbidden_v1(*a, **k):
        raise AssertionError("V1 path must not run when tools are enabled")

    monkeypatch.setattr(pipeline, "review_with_llm", forbidden_v1)
    monkeypatch.setattr(pipeline, "review_with_tools", fake_tools)

    report = run_review(demo_repo, "main", "feature/boundary-bug", use_llm=True, run_tests=False,
                        settings=_settings(tool_calls_enabled=True))
    assert report.tool_status == "ok"
    assert len(report.tool_calls) == 1
    assert report.tool_calls[0].name == "read_code_context"
    assert report.tool_calls[0].ok is True
    assert report.llm_status == "ok"
    # model findings stay suspected, never confirmed
    llm_findings = [f for f in report.final_findings if f.source == FindingSource.LLM]
    assert llm_findings and all(
        f.verification_status == VerificationStatus.SUSPECTED for f in llm_findings
    )
