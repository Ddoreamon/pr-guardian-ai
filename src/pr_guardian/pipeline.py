"""End-to-end review pipeline: diff -> static analysis -> optional tests -> LLM -> validate.

The same function backs the CLI and the web API, so what the page shows is the
real analysis result, never a canned response.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from .agent_tools import TrustedCodebase
from .git_reader import read_changed_files, validate_ref
from .llm_reviewer import review_with_llm
from .models import (
    FindingSource,
    ReviewFinding,
    ReviewReport,
    TestSummary,
    ToolCallRecordModel,
    VerificationStatus,
)
from .settings import Settings, get_settings
from .static_analysis import run_ruff
from .test_runner import run_pytest_on_worktree
from .tool_calling import review_with_tools
from .validator import final_findings, validate_findings


def _run_tests_on_head(repo: Path, head: str) -> TestSummary:
    """Check out `head` into a temporary worktree and run pytest there.

    Only ever called for repositories authored by this project (see
    test_runner for the safety note). Never point this at untrusted code.
    """
    head = validate_ref(head)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "head"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), head],
                capture_output=True, check=True, timeout=60,
            )
            try:
                result = run_pytest_on_worktree(wt)
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                    capture_output=True, check=False, timeout=60,
                )
    except (subprocess.SubprocessError, OSError) as exc:
        return TestSummary(ran=False, skipped_reason=f"could not prepare worktree: {type(exc).__name__}")
    if not result.ran:
        reason = result.reason or "pytest did not produce a usable result"
        if result.timed_out:
            reason = "pytest timed out"
        return TestSummary(ran=False, skipped_reason=reason,
                           timed_out=result.timed_out,
                           duration_seconds=result.duration_seconds)
    return TestSummary(
        ran=True,
        passed=result.passed,
        failed=result.failed,
        errors=result.errors,
        failed_tests=result.failed_tests,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
    )


def run_review(
    repo: Path,
    base: str,
    head: str,
    use_llm: bool = True,
    run_tests: bool = False,
    settings: Settings | None = None,
) -> ReviewReport:
    cfg = settings or get_settings()
    files, diff_text = read_changed_files(repo, base, head)
    notes: list[str] = []

    static, ruff_notes = run_ruff(repo, files)
    notes += ruff_notes

    # ---- tests -------------------------------------------------------------
    test_summary = TestSummary(ran=False)
    if not run_tests:
        test_summary = TestSummary(ran=False, skipped_reason="pytest disabled for this review")
    elif not cfg.allow_test_execution:
        test_summary = TestSummary(
            ran=False,
            skipped_reason="pytest execution disabled by PR_GUARDIAN_ALLOW_TEST_EXECUTION=0",
        )
        notes.append(test_summary.skipped_reason)
    else:
        test_summary = _run_tests_on_head(repo, head)
        if test_summary.ran:
            notes.append(
                f"pytest on head: {test_summary.passed} passed, {test_summary.failed} failed, "
                f"{test_summary.errors} errors, timed_out={test_summary.timed_out}"
            )
            if test_summary.failed or test_summary.errors:
                static.append(
                    ReviewFinding(
                        id="pytest-1",
                        file_path="(test suite)",
                        start_line=1,
                        end_line=1,
                        category="test-failure",
                        severity="high",
                        summary=f"{test_summary.failed + test_summary.errors} test(s) failing on head",
                        explanation="Failing tests: " + ", ".join(test_summary.failed_tests),
                        evidence=f"pytest reported {test_summary.failed} failed, {test_summary.errors} errors",
                        source=FindingSource.PYTEST,
                        verification_status=VerificationStatus.CONFIRMED,
                    )
                )
        else:
            notes.append(test_summary.skipped_reason)

    # ---- LLM ---------------------------------------------------------------
    llm_findings: list[ReviewFinding] = []
    llm_status = cfg.llm_status()
    llm_note = ""
    tool_status = "disabled"
    tool_calls: list[ToolCallRecordModel] = []
    tool_model_calls = 0
    tool_rounds = 0
    if not use_llm:
        llm_status, llm_note = "disabled_by_request", "LLM step disabled for this review."
    elif cfg.tool_calls_enabled:
        # Tools read the immutable snapshot of the ref under review, not the worktree.
        tool_result = review_with_tools(
            diff_text, files, TrustedCodebase.from_git(repo, head), cfg,
            max_rounds=cfg.max_tool_rounds, max_tool_calls=cfg.max_tool_calls,
        )
        llm_findings = tool_result.findings
        notes += tool_result.notes
        llm_note = tool_result.notes[0] if tool_result.notes else ""
        tool_status = tool_result.status
        tool_model_calls = tool_result.model_calls
        tool_rounds = tool_result.rounds
        tool_calls = [ToolCallRecordModel(**r.as_dict()) for r in tool_result.trace]
        if tool_result.status == "not_configured":
            llm_status = "not_configured"
        elif tool_result.status == "disabled":
            llm_status = "disabled_by_public_policy"
        elif tool_result.status == "tool_limit":
            # budget ran out: successful investigation, incomplete coverage
            llm_status = "partial" if tool_result.schema_valid else "failed"
        elif tool_result.schema_valid:
            llm_status = "ok"
        else:
            llm_status = "failed"
        if tool_result.error:
            llm_note = f"{llm_note} ({tool_result.error})".strip()
    else:
        result = review_with_llm(diff_text, files, settings=cfg)
        llm_findings = result.findings
        notes += result.notes
        llm_note = result.notes[0] if result.notes else ""
        if not cfg.llm_configured:
            llm_status = "not_configured"
        elif not cfg.live_llm_enabled:
            llm_status = "disabled_by_public_policy"
        elif result.schema_valid:
            llm_status = "ok"
        else:
            llm_status = "failed"

    validated = validate_findings(static + llm_findings, files)
    return ReviewReport(
        review_id=uuid.uuid4().hex[:12],
        repository=repo.name,
        base_ref=base,
        head_ref=head,
        model_name=cfg.model if llm_status in {"ok", "failed"} else "none",
        changed_files=files,
        static_findings=static,
        llm_findings=llm_findings,
        final_findings=final_findings(validated),
        notes=notes,
        test_summary=test_summary,
        llm_status=llm_status,
        llm_note=llm_note,
        tool_status=tool_status,
        tool_calls=tool_calls,
        tool_model_calls=tool_model_calls,
        tool_rounds=tool_rounds,
    )
