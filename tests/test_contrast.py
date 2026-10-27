"""Tests for the contrast-pair set, its scoring layers and the statistics."""

import json
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
    _located,
    binomial_ge,
    mcnemar_exact,
    semantic_match,
    summarise,
    touched_regions,
)
from pr_guardian.models import FindingSource, ReviewFinding, VerificationStatus


def _finding(line: int, text: str = "s", path: str = "src/app.py") -> ReviewFinding:
    return ReviewFinding(
        id="f", file_path=path, start_line=line, end_line=line, category="logic",
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
    assert semantic_match(pair, [_finding(inside, "this looks odd", pair.diff_file)]) is False
    # located and names the mechanism
    assert semantic_match(
        pair, [_finding(inside, "passes strict as an unexpected keyword argument", pair.diff_file)]) is True
    # correct words but outside the touched definition
    assert semantic_match(
        pair, [_finding(10_000, "unexpected keyword argument", pair.diff_file)]) is False


def test_semantic_match_rejects_the_same_line_in_another_file():
    """Counterexample: right line number and keywords, wrong file."""
    pair = next(p for p in PAIRS if p.pair_id == "P1")
    inside = touched_regions(pair)[0][0]
    wrong_file = _finding(inside, "unexpected keyword argument strict", "src/other.py")
    assert semantic_match(pair, [wrong_file]) is False


def test_semantic_match_requires_every_signature_group():
    """One keyword group is not enough: the signature is deliberately two-part."""
    pair = next(p for p in PAIRS if p.pair_id == "P2")
    inside = touched_regions(pair)[0][0]
    only_units = _finding(inside, "the units look wrong", pair.diff_file)
    assert semantic_match(pair, [only_units]) is False
    both = _finding(inside, "cents are passed into convert, a 100x scale error", pair.diff_file)
    assert semantic_match(pair, [both]) is True


def test_located_must_be_in_the_changed_file():
    """Location scoring must reject a finding in a different file."""
    pair = next(p for p in PAIRS if p.pair_id == "P1")
    inside = touched_regions(pair)[0][0]
    assert _located(pair, [_finding(inside, "s", "src/other.py")]) is False
    assert _located(pair, [_finding(inside, "s", pair.diff_file)]) is True


def test_semantic_is_documented_as_keyword_assisted():
    import inspect

    doc = inspect.getdoc(semantic_match) or ""
    assert "keyword" in doc.lower()
    assert "not a full semantic judgement" in doc.lower() or "NOT a full semantic" in doc


def test_random_baseline_for_a_pair_is_p_times_one_minus_p():
    """A pair is (A=bug, B=clean); a coin flip gets both right with p(1-p).

    At p=0.5 that is 25%, not 50%: 2p(1-p) would also count the case where both
    predictions are wrong.
    """
    for p in (0.2, 0.5, 0.8):
        pair_probability = p * (1 - p)
        both_wrong = p * (1 - p)  # A=clean missed, B=bug invented
        assert abs((pair_probability + both_wrong) - 2 * p * (1 - p)) < 1e-12
        assert pair_probability == (0.25 if p == 0.5 else pair_probability)


def test_binomial_p_values_use_the_pair_null_of_025():
    # dev split, n = 5 decided pairs
    assert binomial_ge(2, 5, 0.25) == 0.367188
    assert binomial_ge(3, 5, 0.25) == 0.103516
    assert binomial_ge(5, 5, 0.25) < 0.01
    assert binomial_ge(0, 5, 0.25) == 1.0


def test_mcnemar_exact_is_two_sided():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(1, 0) == 1.0          # 2 * 0.5**1
    assert mcnemar_exact(3, 0) == 0.25         # 2 * (1/8)
    assert mcnemar_exact(2, 0) == 0.5


# ---- summaries keep accuracy and pair consistency separate ----------------

def _outcome(pid, variant, label, flagged, semantic=False, cls="unambiguous"):
    """Build an outcome using the same classification rule as the evaluator."""
    has_bug = label == "bug"
    if has_bug:
        outcome_class = "TP" if flagged else "FN"
    elif not flagged:
        outcome_class = "TN"
    elif cls == "unambiguous":
        outcome_class = "FP"
    elif semantic:
        outcome_class = "contested"
    else:
        outcome_class = "FP"
    return VariantOutcome(
        pair_id=pid, variant=variant, split="dev", mechanism="M1", label=label,
        flagged=flagged, located=flagged, semantic=semantic,
        correct_location=(flagged == has_bug),
        correct_semantic=(semantic if has_bug else not flagged),
        clean_class=cls, outcome_class=outcome_class,
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


def test_contested_clean_finding_is_not_counted_as_false_positive():
    """A clean variant whose diff really changes the function's own behaviour:
    an accurate description is contested, not a false positive."""
    outcomes = [
        _outcome("P3", "a", "bug", True, semantic=True),
        _outcome("P3", "b", "clean", True, semantic=True, cls="context_dependent"),
    ]
    s = summarise("m", "dev", outcomes, 1)
    assert s.tp == 1 and s.fp == 0
    assert s.contested == 1
    assert s.decided == 1
    assert s.clean_false_alarm_rate is None      # no decided clean variant
    assert s.pairs_decided == 0
    assert s.pair_consistency is None
    assert s.contested_rate == 0.5


def test_unfounded_finding_on_a_contested_pair_is_still_a_false_positive():
    """Context-dependent or not, a claim the diff does not support is an FP."""
    outcomes = [
        _outcome("P3", "a", "bug", True, semantic=True),
        _outcome("P3", "b", "clean", True, semantic=False, cls="context_dependent"),
    ]
    s = summarise("m", "dev", outcomes, 1)
    assert s.fp == 1 and s.contested == 0


def test_contested_pairs_are_excluded_from_pair_consistency():
    outcomes = [
        _outcome("P1", "a", "bug", True, semantic=True),
        _outcome("P1", "b", "clean", False),
        _outcome("P3", "a", "bug", True, semantic=True),
        _outcome("P3", "b", "clean", True, semantic=True, cls="context_dependent"),
    ]
    s = summarise("m", "dev", outcomes, 2)
    assert s.pairs == 2
    assert s.pairs_decided == 1              # P3 excluded
    assert s.pair_consistent == 1
    assert s.pair_consistency == 1.0


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


@pytest.mark.parametrize("pair_id", ["P1", "P3", "P5"])
def test_labels_hold_end_to_end(pair_id):
    pair = next(p for p in PAIRS if p.pair_id == pair_id)
    assert _run(files_for(pair, "a", apply_pr=False), pair_tests(pair, "a")) is True
    assert _run(files_for(pair, "a", apply_pr=True), pair_tests(pair, "a")) is False
    assert _run(files_for(pair, "b", apply_pr=False), pair_tests(pair, "b")) is True
    assert _run(files_for(pair, "b", apply_pr=True), pair_tests(pair, "b")) is True

def test_v1_and_v2_share_the_same_review_scope():
    """The two methods must ask for the same review; only the tools differ."""
    from pr_guardian.llm_reviewer import REVIEW_RULES, REVIEW_SCOPE
    from pr_guardian.tool_calling import SYSTEM_PROMPT

    assert REVIEW_RULES.startswith(REVIEW_SCOPE)
    assert SYSTEM_PROMPT.startswith(REVIEW_SCOPE)
    # the scope must state that green tests are not proof of correctness
    assert "NOT proof" in REVIEW_SCOPE
    assert "annotations" in REVIEW_SCOPE and "docstrings" in REVIEW_SCOPE

def test_experiment_record_captures_the_freeze_and_never_the_key(tmp_path):
    """The record must pin what was frozen, and must not leak the API key."""
    from types import SimpleNamespace

    from benchmark.contrast_cases import VERSION
    from benchmark.evaluate_contrast import experiment_record, write_experiment
    from pr_guardian.settings import Settings

    settings = Settings(
        api_key="sk-should-never-be-written", base_url="https://example.test",
        model="fake-model", public_mode=False, allow_live_llm=True,
        allow_test_execution=True, max_live_llm_calls=50,
        tool_calls_enabled=True, max_tool_rounds=3, max_tool_calls=4,
    )
    args = SimpleNamespace(split="heldout")
    record = experiment_record(args, settings, build_index(),
                               ["v1-diff-only", "v2-tools"], "t0", "t1")
    assert record["dataset_version"] == VERSION
    assert record["model"] == "fake-model"
    assert record["split"] == "heldout"
    assert len(record["pairs"]) == 11
    assert record["api_key_recorded"] is False
    assert "sk-should-never-be-written" not in json.dumps(record)

    write_experiment(tmp_path, record)
    text = (tmp_path / "experiment.json").read_text(encoding="utf-8")
    assert "sk-should-never-be-written" not in text
    assert "dataset_version" in text
    assert (tmp_path / "EXPERIMENT.md").is_file()


def test_variant_outcome_records_tool_status_and_trace():
    o = VariantOutcome(
        pair_id="P1", variant="a", split="dev", mechanism="M1", label="bug",
        flagged=True, located=True, semantic=True, correct_location=True,
        correct_semantic=True, tool_status="tool_limit", partial=True,
        tool_trace=[{"round": 1, "name": "read_code_context", "outcome": "hit"}],
    )
    assert o.partial is True
    assert o.tool_status == "tool_limit"
    assert o.tool_trace[0]["outcome"] == "hit"
