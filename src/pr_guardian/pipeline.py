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


def _reported_model(cfg: Settings, llm_status: str, prompt_tokens: int,
                    completion_tokens: int, tool_model_calls: int) -> str:
    """Name the model only when a request was really made."""
    made_a_call = (
        tool_model_calls > 0
        or prompt_tokens > 0
        or completion_tokens > 0
        or llm_status in {"ok", "failed", "partial"}
    )
    return cfg.model if made_a_call else "none"


def _tool_backend(cfg: Settings, repo: Path, head: str):
    """Return (backend, needs_close). Local is the default; MCP is opt-in."""
    if cfg.tool_backend == "mcp":
        from .mcp_client import MCPToolBackend

        return MCPToolBackend(repo, head), True
    return TrustedCodebase.from_git(repo, head), False
from .evidence import (
    EVIDENCE_DETERMINISTIC,
    EVIDENCE_VERIFIED,
    registry_fingerprint,
    verify_findings,
)
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
    prompt_tokens = 0
    completion_tokens = 0
    tool_calls: list[ToolCallRecordModel] = []
    tool_model_calls = 0
    tool_model_failures = 0
    tool_rounds = 0
    if not use_llm:
        llm_status, llm_note = "disabled_by_request", "LLM step disabled for this review."
    elif cfg.tool_calls_enabled:
        # Tools read the immutable snapshot of the ref under review, not the worktree.
        backend, needs_close = _tool_backend(cfg, repo, head)
        try:
            tool_result = review_with_tools(
                diff_text, files, backend, cfg,
                max_rounds=cfg.max_tool_rounds, max_tool_calls=cfg.max_tool_calls,
            )
        finally:
            if needs_close:
                backend.close()
        llm_findings = tool_result.findings
        notes += tool_result.notes
        llm_note = tool_result.notes[0] if tool_result.notes else ""
        tool_status = tool_result.status
        tool_model_calls = tool_result.model_calls
        tool_model_failures = tool_result.model_failures
        tool_rounds = tool_result.rounds
        tool_calls = [ToolCallRecordModel(**r.as_dict()) for r in tool_result.trace]
        usage = tool_result.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
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
        usage = result.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
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

    # ---- evidence provenance ------------------------------------------------
    # The allowlist and the pinned SHA come from the same trusted codebase the
    # tools use, whatever backend served them (local or MCP).
    scope = TrustedCodebase.from_git(repo, head)
    snapshot_sha = scope.snapshot_sha
    allowed_paths = set(scope.source_files())
    registry = getattr(tool_result, "registry", None) if cfg.tool_calls_enabled else None
    llm_findings = verify_findings(llm_findings, registry, snapshot_sha, allowed_paths)
    static_verified = verify_findings(static, None, snapshot_sha, allowed_paths)

    validated = validate_findings(static_verified + llm_findings, files)
    final = final_findings(validated)
    trusted = [f for f in final
               if f.evidence_status in {EVIDENCE_VERIFIED, EVIDENCE_DETERMINISTIC}]
    return ReviewReport(
        review_id=uuid.uuid4().hex[:12],
        repository=repo.name,
        base_ref=base,
        head_ref=head,
        # a partial run really did call the model: naming it "none" would hide
        # that tokens were spent and that the review is incomplete
        model_name=_reported_model(cfg, llm_status, prompt_tokens, completion_tokens,
                                   tool_model_calls),
        changed_files=files,
        static_findings=static,
        llm_findings=llm_findings,
        final_findings=final,
        evidence_verified=sum(1 for f in final if f.evidence_status == EVIDENCE_VERIFIED),
        evidence_unverified=sum(1 for f in final
                                if f.evidence_status not in {EVIDENCE_VERIFIED,
                                                             EVIDENCE_DETERMINISTIC}),
        trusted_findings=trusted,
        evidence_sources=registry.summary() if registry is not None else [],
        evidence_registry_fingerprint=registry_fingerprint(registry) if registry else "",
        notes=notes,
        test_summary=test_summary,
        llm_status=llm_status,
        llm_note=llm_note,
        llm_prompt_tokens=prompt_tokens,
        llm_completion_tokens=completion_tokens,
        tool_status=tool_status,
        tool_calls=tool_calls,
        tool_model_calls=tool_model_calls,
        tool_model_failures=tool_model_failures,
        tool_rounds=tool_rounds,
    )
