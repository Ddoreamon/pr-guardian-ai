"""Evidence provenance: registry, validator and report compatibility.

No model calls. Tool payloads are produced by the real TrustedCodebase against a
real git snapshot, so the ranges that reach the registry are the ranges a model
would have received.
"""

import json
import subprocess
from pathlib import Path

import pytest

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import (
    CLAIM_DETERMINISTIC,
    CLAIM_UNJUDGED,
    EVIDENCE_DETERMINISTIC,
    EVIDENCE_NO_REF,
    EVIDENCE_UNVERIFIED,
    EVIDENCE_VERIFIED,
    EvidenceRegistry,
    registry_fingerprint,
    validate_ref,
    verify_finding,
)
from pr_guardian.models import (
    EvidenceRef,
    FindingSource,
    ReviewFinding,
    ReviewReport,
    VerificationStatus,
)
from pr_guardian.tool_calling import ToolCallRecord

APP = '''"""App."""

RATE = 0.2


def charge(net, units):
    """Charge a customer."""
    return net * RATE + units
'''

SHIP = '''"""Shipping."""


def cost(weight_kg):
    """Return the shipping cost in dollars."""
    return round(4.5 + weight_kg * 1.25, 2)
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PR_GUARDIAN_CACHE_DIR", str(tmp_path / ".cache"))
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "e@example.com")
    _git(root, "config", "user.name", "e")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app.py").write_text(APP, encoding="utf-8")
    (root / "src" / "shipping.py").write_text(SHIP, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n",
                                                encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _read_record(payload: dict, call_id: str = "call_read_1") -> ToolCallRecord:
    return ToolCallRecord(1, "read_code_context",
                          {"file_path": payload["file_path"],
                           "start_line": payload["start_line"],
                           "end_line": payload["end_line"]},
                          True, "hit", "", "", 100, 0.01, 0, call_id)


def _finding(refs, file_path="src/shipping.py", start=4, end=5, source=FindingSource.LLM):
    return ReviewFinding(
        id="llm-1", file_path=file_path, start_line=start, end_line=end,
        category="logic", severity="medium", summary="s", source=source,
        verification_status=VerificationStatus.SUSPECTED, evidence_refs=refs,
    )


# ---- the happy path --------------------------------------------------------

def test_cross_file_evidence_from_a_real_read_is_verified(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)

    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=4, end_line=5, tool_call_id="call_read_1",
                      tool_name="read_code_context")
    status, reason = validate_ref(ref, registry, codebase.snapshot_sha,
                                  set(codebase.source_files()))
    assert (status, reason) == (EVIDENCE_VERIFIED, "ok")

    verified = verify_finding(_finding([ref]), registry, codebase.snapshot_sha,
                              set(codebase.source_files()))
    assert verified.evidence_status == EVIDENCE_VERIFIED
    # provenance is verified; the claim is not judged and the status is unchanged
    assert verified.claim_status == CLAIM_UNJUDGED
    assert verified.verification_status == VerificationStatus.SUSPECTED


def test_a_narrower_range_inside_the_delivered_range_is_verified(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=5, end_line=5, tool_call_id="call_read_1")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files()))[0] == EVIDENCE_VERIFIED


# ---- refusals --------------------------------------------------------------

def test_forged_file_is_unverified(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/app.py",
                      start_line=1, end_line=2, tool_call_id="call_read_1")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files())) == (EVIDENCE_UNVERIFIED,
                                                          "not_delivered_to_model")


def test_forged_line_range_in_the_same_file_is_unverified(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    # a different region of the same file was never delivered
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=40, end_line=44, tool_call_id="call_read_1")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files()))[0] == EVIDENCE_UNVERIFIED


def test_another_commit_is_rejected(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    ref = EvidenceRef(ref="0" * 40, file_path="src/shipping.py", start_line=4,
                      end_line=5, tool_call_id="call_read_1")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files())) == (EVIDENCE_UNVERIFIED,
                                                          "sha_mismatch")


def test_path_outside_the_allowlist_is_rejected(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="tests/test_app.py",
                      start_line=1, end_line=1, tool_call_id="call_read_1")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files())) == (EVIDENCE_UNVERIFIED,
                                                          "path_not_allowed")


def test_model_supplied_call_id_is_resolved_from_the_registry(repo):
    """Provider call ids are opaque to the model, so provenance is by range.

    The registry attributes the citation to the call that actually delivered it,
    and a mismatched echo is recorded rather than trusted.
    """
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload, call_id="call_real"), payload)
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=4, end_line=5, tool_call_id="call_invented")
    status, _reason = validate_ref(ref, registry, codebase.snapshot_sha,
                                   set(codebase.source_files()))
    assert status == EVIDENCE_VERIFIED

    verified = verify_finding(_finding([ref]), registry, codebase.snapshot_sha,
                              set(codebase.source_files()))
    assert verified.evidence_status == EVIDENCE_VERIFIED
    assert verified.evidence_refs[0].tool_call_id == "call_real"
    assert "model_tool_call_id_unrecognised" in verified.evidence_validation_notes


def test_content_removed_by_the_output_budget_is_not_citable(repo):
    """Only what survived truncation counts as delivered."""
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = {
        "ok": True, "tool": "search_code", "ref": codebase.snapshot_sha,
        "query": "cost", "truncated": True, "omitted": 3,
        "hits": [{"chunk_id": "abc", "file_path": "src/shipping.py",
                  "start_line": 1, "end_line": 3, "qualname": "cost",
                  "kind": "function", "snippet": "def cost", "score": 1.0,
                  "ref": codebase.snapshot_sha}],
        "count": 1, "match_count": 1,
    }
    record = ToolCallRecord(1, "search_code", {"query": "cost"}, True, "hit",
                            "", "", 120, 0.01, 1, "call_search_1")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(record, payload)
    assert [e.end_line for e in registry.entries] == [3]

    # a search hit is a candidate: it can never prove a line was read
    kept = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                       start_line=2, end_line=3, tool_call_id="call_search_1")
    dropped = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                          start_line=4, end_line=6, tool_call_id="call_search_1")
    assert validate_ref(kept, registry, codebase.snapshot_sha,
                        set(codebase.source_files())) == (EVIDENCE_UNVERIFIED,
                                                          "candidate_only")
    assert validate_ref(dropped, registry, codebase.snapshot_sha,
                        set(codebase.source_files()))[0] == EVIDENCE_UNVERIFIED
    assert registry.entries[0].truncated is True
    assert registry.entries[0].citable is False


@pytest.mark.parametrize("outcome", ["miss", "error", "budget_denied"])
def test_non_hit_outcomes_create_no_citable_evidence(repo, outcome):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = {"ok": True, "ref": codebase.snapshot_sha, "hits": [],
               "matches": [], "file_path": "src/shipping.py",
               "start_line": 1, "end_line": 8, "content": "def cost(): ..."}
    record = ToolCallRecord(1, "read_code_context", {"file_path": "src/shipping.py"},
                            False, outcome, "x", "y", 10, 0.01, 0, "call_x")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(record, payload)
    assert registry.entries == []
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=1, end_line=8, tool_call_id="call_x")
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files()))[0] == EVIDENCE_UNVERIFIED


def test_find_symbol_ranges_come_from_the_delivered_snippet(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.find_symbol_definition("cost")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    record = ToolCallRecord(1, "find_symbol_definition", {"symbol": "cost"},
                            True, "hit", "", "", 200, 0.01, 1, "call_sym")
    registry.record(record, payload)
    assert registry.entries
    entry = registry.entries[0]
    assert entry.file_path == "src/shipping.py"
    assert entry.start_line <= 5 <= entry.end_line


# ---- source verified does not mean the claim is right ----------------------

def test_verified_source_does_not_upgrade_the_verdict(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    ref = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                      start_line=4, end_line=6, tool_call_id="call_read_1")
    wrong_claim = _finding([ref]).model_copy(
        update={"summary": "this function mutates its argument",
                "explanation": "it clearly mutates the caller's list"})
    verified = verify_finding(wrong_claim, registry, codebase.snapshot_sha,
                              set(codebase.source_files()))
    assert verified.evidence_status == EVIDENCE_VERIFIED     # source is real
    assert verified.claim_status == CLAIM_UNJUDGED           # correctness unknown
    assert verified.verification_status == VerificationStatus.SUSPECTED


def test_finding_without_citations_is_kept_but_marked(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    marked = verify_finding(_finding([]), registry, codebase.snapshot_sha,
                            set(codebase.source_files()))
    assert marked.evidence_status == EVIDENCE_NO_REF
    assert marked.summary == "s"          # never silently dropped


def test_deterministic_findings_are_not_llm_evidence(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    finding = _finding([], source=FindingSource.RUFF).model_copy(
        update={"verification_status": VerificationStatus.CONFIRMED})
    verified = verify_finding(finding, None, codebase.snapshot_sha, set())
    assert verified.evidence_status == EVIDENCE_DETERMINISTIC
    assert verified.claim_status == CLAIM_DETERMINISTIC
    assert verified.verification_status == VerificationStatus.CONFIRMED


def test_one_bad_citation_taints_the_finding(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload), payload)
    good = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/shipping.py",
                       start_line=4, end_line=5, tool_call_id="call_read_1")
    bad = EvidenceRef(ref=codebase.snapshot_sha, file_path="src/app.py",
                      start_line=1, end_line=2, tool_call_id="call_read_1")
    verified = verify_finding(_finding([good, bad]), registry, codebase.snapshot_sha,
                              set(codebase.source_files()))
    assert verified.evidence_status == EVIDENCE_UNVERIFIED
    assert verified.evidence_validation_notes


def test_registry_fingerprint_is_stable(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    first = EvidenceRegistry(ref=codebase.snapshot_sha)
    first.record(_read_record(payload), payload)
    second = EvidenceRegistry(ref=codebase.snapshot_sha)
    second.record(_read_record(payload), payload)
    assert registry_fingerprint(first) == registry_fingerprint(second)


# ---- report compatibility --------------------------------------------------

def test_old_report_json_still_parses():
    """Reports written before V4.1 have none of the new fields."""
    legacy = {
        "review_id": "abc123", "repository": "demo", "base_ref": "main",
        "head_ref": "feature", "model_name": "none",
        "changed_files": [{"file_path": "src/a.py", "change_type": "modified"}],
        "final_findings": [{
            "id": "f1", "file_path": "src/a.py", "start_line": 1, "end_line": 2,
            "category": "logic", "severity": "low", "summary": "s",
            "source": "llm", "verification_status": "suspected",
        }],
        "created_at": "2026-01-01T00:00:00Z",
    }
    report = ReviewReport.model_validate(legacy)
    assert report.final_findings[0].evidence_status == "not_evaluated"
    assert report.final_findings[0].evidence_refs == []
    assert report.evidence_verified == 0
    assert report.trusted_findings == []
    # and it round-trips with the new fields present
    again = ReviewReport.model_validate(json.loads(report.model_dump_json()))
    assert again.final_findings[0].summary == "s"

def test_a_nonsense_ref_is_a_formatting_slip_not_another_commit(repo):
    """The commit is pinned by the review; the model cannot select one."""
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload, call_id="call_real"), payload)
    ref = EvidenceRef(ref="the diff hunk", file_path="src/shipping.py",
                      start_line=4, end_line=5)
    status, _reason = validate_ref(ref, registry, codebase.snapshot_sha,
                                  set(codebase.source_files()))
    assert status == EVIDENCE_VERIFIED
    verified = verify_finding(_finding([ref]), registry, codebase.snapshot_sha,
                              set(codebase.source_files()))
    assert verified.evidence_status == EVIDENCE_VERIFIED
    assert verified.evidence_refs[0].ref == codebase.snapshot_sha


def test_a_real_sha_from_another_commit_is_refused(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/shipping.py", 1, 8)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_read_record(payload, call_id="call_real"), payload)
    ref = EvidenceRef(ref="f" * 40, file_path="src/shipping.py",
                      start_line=4, end_line=5)
    assert validate_ref(ref, registry, codebase.snapshot_sha,
                        set(codebase.source_files())) == (EVIDENCE_UNVERIFIED,
                                                          "sha_mismatch")
