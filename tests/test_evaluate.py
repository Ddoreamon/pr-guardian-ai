"""Unit tests for benchmark metric logic, using hand-computed expected values."""

from benchmark.cases import Case
from benchmark.evaluate import CaseOutcome, f1, match_findings, safe_div, summarise
from pr_guardian.models import FindingSource, ReviewFinding, VerificationStatus


def _case(has_bug: bool) -> Case:
    return Case(case_id="T-01", category="boundary", title="t", module="m",
                base_code="", pr_code="", test_code="", has_bug=has_bug,
                bug_keyword="", dev_split=True)


def _f(path: str, start: int, end: int | None = None) -> ReviewFinding:
    return ReviewFinding(id="x", file_path=path, start_line=start, end_line=end or start,
                         category="c", severity="low", summary="s",
                         source=FindingSource.RUFF,
                         verification_status=VerificationStatus.CONFIRMED)


# ---- finding-level matching -------------------------------------------------

def test_buggy_case_single_correct_finding_is_one_tp():
    tp, fp, located = match_findings(_case(True), [_f("src/m.py", 5)], [5])
    assert (tp, fp, located) == (1, 0, True)


def test_buggy_case_wrong_line_is_fp_not_tp():
    tp, fp, located = match_findings(_case(True), [_f("src/m.py", 99)], [5])
    assert (tp, fp, located) == (0, 1, False)


def test_buggy_case_duplicate_hits_count_one_tp_and_rest_fp():
    tp, fp, located = match_findings(_case(True), [_f("src/m.py", 5), _f("src/m.py", 5)], [5])
    assert (tp, fp, located) == (1, 1, True)


def test_clean_case_any_finding_on_module_is_fp():
    tp, fp, located = match_findings(_case(False), [_f("src/m.py", 5)], [5])
    assert (tp, fp, located) == (0, 1, False)


def test_finding_on_other_file_ignored():
    tp, fp, located = match_findings(_case(True), [_f("src/other.py", 5)], [5])
    assert (tp, fp, located) == (0, 0, False)


# ---- ratios never divide by zero --------------------------------------------

def test_safe_div_zero_denominator_is_none():
    assert safe_div(0, 0) is None
    assert safe_div(3, 0) is None


def test_f1_undefined_when_no_precision_or_recall():
    assert f1(None, 0.5) is None
    assert f1(0.0, 0.0) is None
    assert f1(0.5, 0.5) == 0.5


# ---- summary metrics ---------------------------------------------------------

def _o(has_bug, flagged, located=None, status="ok", tp=0, fp=0, split="dev"):
    return CaseOutcome("c", split, "x", has_bug, flagged, 0, tp, fp, located, 0.1, status)


def test_summary_pr_level_precision_recall_hand_computed():
    cases = [_case(True)] * 4 + [_case(False)] * 2
    outcomes = [
        _o(True, True, True, tp=1),    # TP
        _o(True, True, True, tp=1),    # TP
        _o(True, False, False),        # FN
        _o(True, False, False),        # FN
        _o(False, True, tp=0, fp=1),   # FP
        _o(False, False),              # TN
    ]
    s = summarise("m", cases, outcomes, [])
    assert (s.pr_tp, s.pr_fp, s.pr_fn, s.pr_tn) == (2, 1, 2, 1)
    assert s.pr_precision == round(2 / 3, 4)
    assert s.pr_recall == 0.5
    assert s.clean_false_alarm_rate == 0.5
    assert s.bug_location_accuracy == 0.5


def test_summary_zero_predictions_no_crash():
    cases = [_case(True), _case(False)]
    outcomes = [_o(True, False, False), _o(False, False)]
    s = summarise("m", cases, outcomes, [])
    assert s.pr_precision is None          # zero predicted positives
    assert s.pr_recall == 0.0
    assert s.pr_f1 is None
    assert s.clean_false_alarm_rate == 0.0


def test_summary_no_bug_cases_recall_undefined():
    cases = [_case(False), _case(False)]
    outcomes = [_o(False, True, fp=1), _o(False, False)]
    s = summarise("m", cases, outcomes, [])
    assert s.pr_recall is None
    assert s.clean_false_alarm_rate == 0.5


def test_not_evaluated_cases_are_excluded_from_metrics():
    cases = [_case(True), _case(False)]
    outcomes = [
        CaseOutcome("c1", "dev", "x", True, False, 0, 0, 0, None, 0.0, "not_evaluated", "no key"),
        _o(False, False),
    ]
    s = summarise("m", cases, outcomes, ["no key"])
    assert s.status == "partial"
    assert s.pr_tp + s.pr_fn == 0          # the bug case is not counted as a miss
    assert s.n_cases == 2


def test_all_not_evaluated_status_is_not_evaluated():
    cases = [_case(True)]
    outcomes = [CaseOutcome("c1", "dev", "x", True, False, 0, 0, 0, None, 0.0, "not_evaluated", "no key")]
    s = summarise("llm-only", cases, outcomes, ["no key"])
    assert s.status == "not_evaluated"
    assert s.pr_precision is None and s.pr_recall is None


# ---- method definitions: llm-only is raw, combined applies the validator ----

def test_combined_applies_validator_but_llm_only_does_not(monkeypatch, tmp_path):
    """A finding on a line outside the diff must survive llm-only but be dropped
    by combined, because combined is the shipped pipeline."""
    from benchmark import evaluate as ev
    from benchmark.cases import build_cases
    from pr_guardian.llm_reviewer import LLMResult
    from pr_guardian.models import FindingSource, VerificationStatus

    case = next(c for c in build_cases() if c.has_bug and c.dev_split)
    changed_hint = None

    def fake_review(diff_text, files, settings=None):
        nonlocal changed_hint
        changed_hint = min(files[0].added_lines)
        good = ReviewFinding(id="a", file_path=f"src/{case.module}.py",
                             start_line=changed_hint, end_line=changed_hint,
                             category="boundary", severity="medium", summary="real",
                             source=FindingSource.LLM,
                             verification_status=VerificationStatus.SUSPECTED)
        bad = ReviewFinding(id="b", file_path="src/nowhere.py", start_line=999,
                            end_line=999, category="boundary", severity="medium",
                            summary="hallucinated", source=FindingSource.LLM,
                            verification_status=VerificationStatus.SUSPECTED)
        return LLMResult([good, bad], ["LLM model: fake"], True, model="fake",
                         latency_s=0.01, usage={"prompt_tokens": 10, "completion_tokens": 5})

    monkeypatch.setenv("PR_GUARDIAN_API_KEY", "test-key")
    monkeypatch.setattr("pr_guardian.llm_reviewer.review_with_llm", fake_review)

    raw = ev.run_method(ev.METHOD_LLM, case, tmp_path)
    combined = ev.run_method(ev.METHOD_COMBINED, case, tmp_path)

    assert len(raw.findings) == 2, "llm-only must keep raw model output"
    assert all(f.file_path == f"src/{case.module}.py" for f in combined.findings), \
        "combined must drop findings that are not in the diff"
    assert len(combined.findings) == 1
    assert combined.prompt_tokens == 10 and combined.completion_tokens == 5
    assert combined.llm_called is True


# ---- the diff must not leak or fake the ground-truth tests ------------------

def test_non_test_methods_never_see_the_test_file(tmp_path):
    """The diff must contain only the module edit — no test file, no deletion."""
    from benchmark.cases import build_cases
    from benchmark.evaluate import materialise_case
    from pr_guardian.git_reader import read_changed_files

    case = build_cases()[0]
    repo = materialise_case(case, tmp_path, include_tests=False)

    files, diff = read_changed_files(repo, "main", "pr")
    assert [f.file_path for f in files] == [f"src/{case.module}.py"]
    assert "tests/" not in diff, "a deleted/added test file would be a spurious change"
    assert not (repo / "tests").exists()


def test_plus_tests_condition_has_tests_in_both_revisions(tmp_path):
    """With +tests the tests exist and are unchanged, so they do not appear as a diff."""
    from benchmark.cases import build_cases
    from benchmark.evaluate import materialise_case
    from pr_guardian.git_reader import read_changed_files

    case = build_cases()[0]
    repo = materialise_case(case, tmp_path, include_tests=True)

    files, _diff = read_changed_files(repo, "main", "pr")
    assert [f.file_path for f in files] == [f"src/{case.module}.py"]
    assert (repo / "tests" / f"test_{case.module}.py").is_file()
