"""Unit and integration tests for the V4.5 sufficiency gate. No model calls."""

import ast
import inspect
import tempfile
from pathlib import Path

import pytest

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.git_reader import read_changed_files
from pr_guardian.sufficiency import (
    CALLEE_CONTRACT,
    CALLER_USAGE,
    INSUFFICIENT,
    MODIFIED_DEFINITION,
    SUFFICIENT,
    UNKNOWN,
    UNKNOWN_DEPENDENCY,
    EvidenceObligation,
    EvidenceSufficiencyResult,
    assess,
    derive_obligations,
)
from pr_guardian.tool_calling import ToolCallRecord
from scripts.sufficiency_cases import CASES as HELD_CASES
from scripts.sufficiency_cases import build_case


@pytest.fixture(scope="module")
def repos():
    root = Path(tempfile.mkdtemp(prefix="v45-tests-"))
    built = {}
    for case in HELD_CASES:
        repo = build_case(root, case)
        branch = "case/" + case["id"].lower()
        head = TrustedCodebase.from_git(repo, branch)
        base = TrustedCodebase.from_git(repo, "main")
        files, _ = read_changed_files(repo, "main", branch)
        built[case["id"]] = {
            "head": head, "base": base, "files": files,
            "obligations": derive_obligations(head, base, files),
            "allowed": set(head.source_files()),
        }
    return built


def _kinds(obligations):
    return sorted({o.kind for o in obligations})


def _read(codebase, path, start=1, end=None):
    end = end or len(codebase.read_source(path).splitlines())
    payload = codebase.read_code_context(path, start, end)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(ToolCallRecord(1, "read_code_context",
                                   {"file_path": path, "start_line": start,
                                    "end_line": end}, True, "hit", "", "", 1,
                                   0.0, 0, "call-1"), payload)
    return registry


# ---- derivation ------------------------------------------------------------

def test_derivation_uses_only_snapshot_diff_and_ast():
    """The public signature must not accept gold, labels or case identifiers."""
    params = list(inspect.signature(derive_obligations).parameters)
    assert params == ["head", "base", "changed_files"]


def test_signature_change_requires_the_callers(repos):
    kinds = _kinds(repos["H1-signature"]["obligations"])
    assert CALLER_USAGE in kinds


def test_return_shape_change_requires_the_callers(repos):
    kinds = _kinds(repos["H2-return"]["obligations"])
    assert CALLER_USAGE in kinds


def test_same_file_caller_is_required(repos):
    """Regression: the first version only looked at cross-file edges."""
    obligations = repos["H7-same-file"]["obligations"]
    callers = [o for o in obligations if o.kind == CALLER_USAGE]
    assert callers, "a same-file caller must create an obligation"
    assert callers[0].file_path == "src/cart.py"


def test_local_change_does_not_demand_other_files(repos):
    kinds = _kinds(repos["H6-local"]["obligations"])
    assert kinds == [MODIFIED_DEFINITION]


def test_pure_refactor_does_not_demand_the_caller(repos):
    """A rewrite with the same contract must not force a caller read."""
    kinds = _kinds(repos["H5-config"]["obligations"])
    assert CALLER_USAGE not in kinds


def test_callee_contract_is_derived_for_cross_file_calls(repos):
    kinds = _kinds(repos["H4-multicaller"]["obligations"])
    assert CALLEE_CONTRACT in kinds


def test_dynamic_dispatch_is_flagged_unknown(repos):
    kinds = _kinds(repos["H3-dynamic"]["obligations"])
    assert UNKNOWN_DEPENDENCY in kinds
    dynamic = next(o for o in repos["H3-dynamic"]["obligations"]
                   if o.kind == UNKNOWN_DEPENDENCY)
    assert dynamic.required is False
    assert dynamic.confidence == "low"


def test_obligations_carry_provenance_fields(repos):
    obligation = repos["H1-signature"]["obligations"][0]
    assert obligation.obligation_id.startswith("ob-")
    assert obligation.derivation_method
    assert obligation.rationale
    assert obligation.required_span is not None
    assert obligation.confidence in {"high", "medium", "low"}


# ---- assessment ------------------------------------------------------------

def test_complete_evidence_is_sufficient(repos):
    case = repos["H1-signature"]
    registry = _read(case["head"], "src/formatting.py")
    registry2 = _read(case["head"], "src/report.py")
    for entry in registry2.entries:
        registry.entries.append(entry)
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == SUFFICIENT
    assert result.missing_obligations == []
    assert result.fulfilled_obligations


def test_missing_caller_is_insufficient(repos):
    case = repos["H1-signature"]
    registry = _read(case["head"], "src/formatting.py")
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == INSUFFICIENT
    assert result.missing_obligations


def test_dynamic_construct_makes_the_verdict_unknown(repos):
    case = repos["H3-dynamic"]
    registry = _read(case["head"], "src/plugin.py")
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == UNKNOWN
    assert result.unknown_obligations
    assert "proves coverage" not in result.explanation


def test_search_candidates_never_satisfy_an_obligation(repos):
    case = repos["H1-signature"]
    registry = EvidenceRegistry(ref=case["head"].snapshot_sha)
    payload = case["head"].search_code("render", 5)
    registry.record(ToolCallRecord(1, "search_code", {"query": "render"}, True,
                                   "hit", "", "", 1, 0.0, 1, "search-1"), payload)
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == INSUFFICIENT


def test_evidence_from_another_sha_is_refused(repos):
    case = repos["H1-signature"]
    registry = EvidenceRegistry(ref="f" * 40)
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == INSUFFICIENT


def test_truncated_read_is_not_enough(repos):
    case = repos["H6-local"]
    registry = _read(case["head"], "src/queue.py", 1, 1)
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    assert result.status == INSUFFICIENT


def test_result_is_serialisable_and_versioned(repos):
    case = repos["H6-local"]
    registry = _read(case["head"], "src/queue.py")
    result = assess(registry, case["obligations"], case["head"].snapshot_sha,
                    case["allowed"])
    dumped = result.model_dump(mode="json")
    assert dumped["assessment_version"]
    assert dumped["snapshot_sha"] == case["head"].snapshot_sha
    again = EvidenceSufficiencyResult.model_validate(dumped)
    assert again.status == result.status


def test_obligation_model_round_trips():
    obligation = EvidenceObligation(
        obligation_id="ob-1", kind=MODIFIED_DEFINITION, file_path="src/a.py",
        qualname="f", required_span=(1, 3), rationale="r",
        derivation_method="m", confidence="high", required=True)
    assert EvidenceObligation.model_validate(obligation.model_dump()).kind == \
        MODIFIED_DEFINITION


def test_no_repo_access_is_opened(repos):
    """The gate receives a codebase and a registry; it never reads new files."""
    source = inspect.getsource(assess)
    tree = ast.parse(source)
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "read_code_context" not in calls
    assert "read_source" not in calls
    assert "search_code" not in calls
