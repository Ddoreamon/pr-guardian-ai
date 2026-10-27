"""Tests for the contrast-pair set, its scoring layers and the statistics."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from benchmark.contrast_cases import (
    PAIRS,
    build_index,
    files_for,
)
from benchmark.contrast_cases import (
    tests_for as pair_tests,
)
from benchmark.evaluate_contrast import (
    VariantOutcome,
    binomial_ge,
    semantic_match,
    summarise,
    touched_regions,
)
from pr_guardian.models import FindingSource, ReviewFinding, VerificationStatus


def _finding(line: int, text: str = "s") -> ReviewFinding:
    return ReviewFinding(
        id="f", file_path="src/x.py", start_line=line, end_line=line, category="logic",
        severity="medium", summary=text, source=FindingSource.LLM,
        verification_status=VerificationStatus.SUSPECTED,
    )


# ---- the frozen set --------------------------------------------------------

def test_pair_set_is_frozen_and_balanced():
    pairs = build_index()
    assert len(pairs) == 11
    assert sum(p.split == "dev" for p in pairs) == 5
    assert sum(p.split == "heldout" for p in pairs) == 6
    # one bug and one clean variant per pair
    for pair in pairs:
        assert pair.pr_code != pair.base_code
        assert pair.tests_a and pair.tests_b
        assert pair.defect_signature


def test_context_never_touches_the_changed_file():
    for pair in PAIRS:
        assert pair.diff_file not in pair.context_a
        assert pair.diff_file not in pair.context_b


def test_variants_differ_only_in_the_context_file():
    for pair in PAIRS:
        a = files_for(pair, "a", apply_pr=False)
        b = files_for(pair, "b", apply_pr=False)
        assert set(a) == set(b)
        differing = {k for k in a if a[k] != b[k]}
        assert differing, pair.pair_id
        assert pair.diff_file not in differing
        # the changed file is identical in both base revisions
        assert a[pair.diff_file] == b[pair.diff_file]


def test_variants_have_tests_that_pin_their_own_behaviour():
    """Pairs may share a test file (same spec, different context) — that is fine.
    What matters is that each variant's tests actually assert something."""
    for pair in PAIRS:
        for code in (pair.tests_a, pair.tests_b):
            assert "assert " in code or "pytest.raises" in code


# ---- scoring layers --------------------------------------------------------

def test_touched_regions_cover_the_changed_function():
    pair = next(p for p in PAIRS if p.pair_id == "P1")
    regions = touched_regions(pair)
    assert regions
    first, last = regions[0]
    assert first <= last


def test_semantic_match_requires_the_signature_not_just_location():
    pair = next(p for p in PAIRS if p.pair_id == "P1")
    regions = touched_regions(pair)
    inside = regions[0][0]
    # located but says nothing about the defect
    assert semantic_match(pair, [_finding(inside, "this looks odd")]) is False
    # located and names the mechanism
    assert semantic_match(pair, [_finding(inside, "passes an unexpected keyword argument")]) is True
    # correct words but outside the touched definition
    assert semantic_match(pair, [_finding(10_000, "unexpected keyword argument")]) is False


def test_binomial_p_values_match_the_design_document():
    assert binomial_ge(11, 11) < 0.001
    assert 0.02 < binomial_ge(9, 11) < 0.05     # ~0.033
    assert 0.10 < binomial_ge(8, 11) < 0.13     # ~0.113
    assert binomial_ge(0, 11) == 1.0


# ---- summaries keep accuracy and pair consistency separate ----------------

def _outcome(pid, variant, label, flagged, semantic=False, correct=None):
    has_bug = label == "bug"
    correct_location = (flagged == has_bug)
    return VariantOutcome(
        pair_id=pid, variant=variant, split="dev", mechanism="M1", label=label,
        flagged=flagged, located=flagged and has_bug, semantic=semantic,
        correct_location=correct_location,
        correct_semantic=(semantic if has_bug else not flagged),
    )


def test_always_bug_scores_50_percent_accuracy_and_zero_pair_consistency():
    outcomes = []
    for i in range(5):
        pid = f"P{i}"
        outcomes.append(_outcome(pid, "a", "bug", True))
        outcomes.append(_outcome(pid, "b", "clean", True))   # false alarm
    s = summarise("m", "dev", outcomes, 5)
    assert s.accuracy == 0.5
    assert s.recall == 1.0
    assert s.precision == 0.5
    assert s.pair_consistent == 0
    assert s.pair_consistency == 0.0


def test_always_clean_scores_50_percent_accuracy_and_undefined_precision():
    outcomes = []
    for i in range(5):
        pid = f"P{i}"
        outcomes.append(_outcome(pid, "a", "bug", False))
        outcomes.append(_outcome(pid, "b", "clean", False))
    s = summarise("m", "dev", outcomes, 5)
    assert s.accuracy == 0.5
    assert s.recall == 0.0
    assert s.precision is None
    assert s.pair_consistency == 0.0


def test_using_context_scores_high_pair_consistency():
    outcomes = []
    for i in range(5):
        pid = f"P{i}"
        outcomes.append(_outcome(pid, "a", "bug", True, semantic=True))
        outcomes.append(_outcome(pid, "b", "clean", False))
    s = summarise("m", "dev", outcomes, 5)
    assert s.accuracy == 1.0
    assert s.pair_consistent == 5
    assert s.pair_consistency == 1.0
    assert s.semantic_pair_consistency == 1.0


def test_located_but_wrong_explanation_is_not_semantically_correct():
    outcomes = [
        _outcome("P1", "a", "bug", True, semantic=False),
        _outcome("P1", "b", "clean", False),
    ]
    s = summarise("m", "dev", outcomes, 1)
    assert s.pair_consistent == 1          # location looks right
    assert s.semantic_pair_consistent == 0  # but it never named the defect


# ---- labels are proven by running the tests -------------------------------

def _run(files: dict[str, str], test_code: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_case.py").write_text(test_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, timeout=180, check=False,
        )
        return proc.returncode == 0


@pytest.mark.parametrize("pair_id", ["P1", "P5"])
def test_labels_hold_end_to_end(pair_id):
    pair = next(p for p in PAIRS if p.pair_id == pair_id)
    assert _run(files_for(pair, "a", apply_pr=False), pair_tests(pair, "a")) is True
    assert _run(files_for(pair, "a", apply_pr=True), pair_tests(pair, "a")) is False
    assert _run(files_for(pair, "b", apply_pr=False), pair_tests(pair, "b")) is True
    assert _run(files_for(pair, "b", apply_pr=True), pair_tests(pair, "b")) is True
