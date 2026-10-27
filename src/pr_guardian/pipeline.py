"""End-to-end review pipeline: diff -> static analysis -> LLM -> validate -> report."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from .git_reader import read_changed_files, validate_ref
from .llm_reviewer import review_with_llm
from .models import FindingSource, ReviewFinding, ReviewReport, VerificationStatus
from .static_analysis import run_ruff
from .test_runner import TestResult, run_pytest_on_worktree
from .validator import final_findings, validate_findings


def _run_tests_on_head(repo: Path, head: str) -> TestResult:
    """Check out `head` into a temporary git worktree and run pytest there.

    Only for TRUSTED local fixtures; see test_runner for the safety note.
    """
    head = validate_ref(head)
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "head"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), head],
            capture_output=True, check=True, timeout=60,
        )
        try:
            return run_pytest_on_worktree(wt)
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                capture_output=True, check=False, timeout=60,
            )


def run_review(
    repo: Path,
    base: str,
    head: str,
    use_llm: bool = True,
    run_tests: bool = False,
) -> ReviewReport:
    files, diff_text = read_changed_files(repo, base, head)
    notes: list[str] = []

    static, ruff_notes = run_ruff(repo, files)
    notes += ruff_notes

    test_result: TestResult | None = None
    if run_tests:
        test_result = _run_tests_on_head(repo, head)
        notes.append(
            f"pytest on head: {test_result.passed} passed, {test_result.failed} failed, "
            f"{test_result.errors} errors, timed_out={test_result.timed_out}"
        )
        if test_result.failed or test_result.errors:
            static.append(
                ReviewFinding(
                    id="pytest-1",
                    file_path="(test suite)",
                    start_line=1,
                    end_line=1,
                    category="test-failure",
                    severity="high",
                    summary=f"{test_result.failed + test_result.errors} test(s) failing on head",
                    explanation="Failing tests: " + ", ".join(test_result.failed_tests),
                    evidence=f"pytest exit code {test_result.exit_code}",
                    source=FindingSource.PYTEST,
                    verification_status=VerificationStatus.CONFIRMED,
                )
            )

    llm_findings = []
    if use_llm:
        result = review_with_llm(diff_text, files)
        llm_findings = result.findings
        notes += result.notes
        model_name = result.notes[0].replace("LLM model: ", "") if result.schema_valid else "none"
    else:
        model_name = "disabled"
        notes.append("LLM step disabled by request.")

    validated = validate_findings(static + llm_findings, files)
    return ReviewReport(
        review_id=uuid.uuid4().hex[:12],
        repository=repo.name,
        base_ref=base,
        head_ref=head,
        model_name=model_name,
        changed_files=files,
        static_findings=static,
        llm_findings=llm_findings,
        final_findings=final_findings(validated),
        notes=notes,
    )
