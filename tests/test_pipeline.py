"""Integration tests: real git repo, real Ruff and pytest, no LLM."""

from pr_guardian.models import FindingSource, VerificationStatus
from pr_guardian.pipeline import run_review


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
