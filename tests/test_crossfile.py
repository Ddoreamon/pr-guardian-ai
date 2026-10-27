"""Tests for the cross-file cases and their matching rule (no model calls)."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from benchmark.crossfile_cases import FILES, build_cases, files_for
from benchmark.evaluate_crossfile import match
from pr_guardian.models import FindingSource, ReviewFinding, VerificationStatus


def _finding(path: str, line: int) -> ReviewFinding:
    return ReviewFinding(
        id="f", file_path=path, start_line=line, end_line=line, category="logic",
        severity="medium", summary="s", source=FindingSource.LLM,
        verification_status=VerificationStatus.SUSPECTED,
    )


def test_case_set_is_frozen():
    cases = build_cases()
    assert len(cases) == 10
    assert sum(c.has_bug for c in cases) == 7
    assert sum(c.split == "dev" for c in cases) == 4
    assert sum(c.split == "heldout" for c in cases) == 6
    assert len({c.case_id for c in cases}) == 10


def test_pr_revision_changes_exactly_one_file():
    for case in build_cases():
        base = files_for(case, apply_pr=False)
        pr = files_for(case, apply_pr=True)
        changed = {k for k in base if base[k] != pr[k]}
        assert changed == {case.changed_file}, case.case_id
        assert set(pr) == set(FILES)


def test_every_case_has_expected_text():
    for case in build_cases():
        assert case.expected.strip()
        assert case.test_code.strip()


def test_matching_counts_only_changed_lines():
    case = next(c for c in build_cases() if c.has_bug)
    lines = [5]
    assert match(case, [_finding(case.changed_file, 5)], lines) == (1, 0, True)
    assert match(case, [_finding(case.changed_file, 9)], lines) == (0, 1, False)
    assert match(case, [_finding("src/other.py", 5)], lines) == (0, 0, False)


def test_clean_case_any_finding_on_changed_file_is_fp():
    case = next(c for c in build_cases() if not c.has_bug)
    tp, fp, located = match(case, [_finding(case.changed_file, 1)], [1])
    assert tp == 0 and fp == 1 and located is False


def _run_case_tests(case, apply_pr: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, content in files_for(case, apply_pr=apply_pr).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_case.py").write_text(case.test_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, timeout=120, check=False,
        )
        return proc.returncode == 0


@pytest.mark.parametrize("case_id", ["XF-01", "XF-04"])
def test_labels_agree_with_tests(case_id):
    """One bug case and one clean case are proven end to end in the suite."""
    case = next(c for c in build_cases() if c.case_id == case_id)
    assert _run_case_tests(case, apply_pr=False) is True
    # a bug PR must fail its tests; a clean PR must pass them
    assert _run_case_tests(case, apply_pr=True) is (not case.has_bug)
