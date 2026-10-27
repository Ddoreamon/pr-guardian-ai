"""Tests for the trusted demo repository builder and its failure reporting."""

from pathlib import Path

from app import demo_cases
from app.demo_cases import DemoCase, DemoRepo


def test_demo_cases_are_well_formed():
    assert len(demo_cases.DEMO_CASES) == 3
    ids = [c.case_id for c in demo_cases.DEMO_CASES]
    assert ids == ["boundary-bug", "clean", "injection"]
    for case in demo_cases.DEMO_CASES:
        assert isinstance(case, DemoCase)
        assert case.branch.startswith("feature/")
        assert case.title and case.description and case.expectation


def test_repo_builds_and_has_expected_branches():
    repo = DemoRepo()
    try:
        path = repo.path()
        assert path.is_dir()
        assert (path / "src" / "discount.py").is_file()
        assert (path / "tests" / "test_discount.py").is_file()
        # building twice returns the same cached path
        assert repo.path() == path
    finally:
        repo.reset()


def test_build_failure_is_reported_not_hidden(monkeypatch):
    def boom(_target: Path) -> None:
        raise OSError("git is not available")

    monkeypatch.setattr(demo_cases, "build", boom)
    repo = DemoRepo()
    try:
        try:
            repo.path()
        except RuntimeError as exc:
            assert "could not build demo repository" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
        assert "git is not available" in repo.error
    finally:
        repo.reset()
