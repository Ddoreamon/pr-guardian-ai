"""Pipeline-level provenance: model naming, partial status, registry roll-up."""

import subprocess
from pathlib import Path

import pytest

from pr_guardian import pipeline
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.models import (
    EvidenceRef,
    FindingSource,
    ReviewFinding,
    VerificationStatus,
)
from pr_guardian.settings import Settings
from pr_guardian.tool_calling import ToolCallRecord, ToolReviewResult


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "deepseek-flash",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": False,
        "max_live_llm_calls": 50, "tool_calls_enabled": True,
        "max_tool_rounds": 3, "max_tool_calls": 4,
    }
    base.update(over)
    return Settings(**base)


def _finding(refs, path="src/discount.py", start=4, end=6):
    return ReviewFinding(
        id="llm-1", file_path=path, start_line=start, end_line=end,
        category="logic", severity="high", summary="s", source=FindingSource.LLM,
        verification_status=VerificationStatus.SUSPECTED, evidence_refs=refs,
    )


def _partial_result(codebase, finding):
    payload = codebase.read_code_context("src/discount.py", 1, 6)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(ToolCallRecord(1, "read_code_context",
                                   {"file_path": "src/discount.py", "start_line": 1,
                                    "end_line": 6}, True, "hit", "", "", 90, 0.01, 0,
                                   "call_read_1"), payload)
    return ToolReviewResult(
        findings=[finding], notes=["PARTIAL: the tool budget ran out."],
        schema_valid=True, model="deepseek-flash",
        usage={"prompt_tokens": 1200, "completion_tokens": 90}, rounds=3,
        model_attempts=3, model_successes=3, model_failures=0,
        trace=[ToolCallRecord(1, "read_code_context", {}, True, "hit", "", "", 90,
                              0.01, 0, "call_read_1")],
        status="tool_limit", error="", registry=registry,
    )


def test_partial_run_reports_the_real_model_and_tokens(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, "feature/boundary-bug")
    finding = _finding([EvidenceRef(ref=codebase.snapshot_sha,
                                    file_path="src/discount.py", start_line=4,
                                    end_line=6, tool_call_id="call_read_1",
                                    tool_name="read_code_context")])
    monkeypatch.setattr(pipeline, "review_with_tools",
                        lambda *a, **k: _partial_result(codebase, finding))
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False, settings=_settings())

    # a partial run did call the model: it must not be reported as "none"
    assert report.model_name == "deepseek-flash"
    assert report.llm_status == "partial"
    assert report.llm_prompt_tokens == 1200
    assert report.llm_completion_tokens == 90
    assert report.tool_model_calls == 3
    # and it must not look like a finished clean review
    assert any("PARTIAL" in note for note in report.notes)
    assert report.final_findings, "a partial run must not silently drop findings"


def test_partial_finding_with_real_citation_is_source_verified(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, "feature/boundary-bug")
    finding = _finding([EvidenceRef(ref=codebase.snapshot_sha,
                                    file_path="src/discount.py", start_line=4,
                                    end_line=6, tool_call_id="call_read_1")])
    monkeypatch.setattr(pipeline, "review_with_tools",
                        lambda *a, **k: _partial_result(codebase, finding))
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False, settings=_settings())

    assert report.evidence_sources, "the registry must be reported"
    assert report.evidence_registry_fingerprint
    verified = [f for f in report.final_findings
                if f.evidence_status == "source_verified"]
    assert verified, "a citation to a delivered range should verify"
    # provenance verified, claim still unjudged, verdict not upgraded
    assert verified[0].claim_status == "claim_unjudged"
    assert verified[0].verification_status == VerificationStatus.SUSPECTED
    assert report.trusted_findings


def test_invented_citation_is_reported_as_unverified(demo_repo, monkeypatch):
    codebase = TrustedCodebase.from_git(demo_repo, "feature/boundary-bug")
    finding = _finding([EvidenceRef(ref=codebase.snapshot_sha,
                                    file_path="src/discount.py", start_line=90,
                                    end_line=99, tool_call_id="call_read_1")])
    monkeypatch.setattr(pipeline, "review_with_tools",
                        lambda *a, **k: _partial_result(codebase, finding))
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False, settings=_settings())

    assert report.evidence_verified == 0
    assert report.evidence_unverified >= 1
    assert report.trusted_findings == []
    # the finding is still visible, flagged, not deleted
    assert report.final_findings
    assert report.final_findings[0].evidence_status == "source_unverified"
    assert report.final_findings[0].evidence_validation_notes


def test_unconfigured_review_names_no_model(demo_repo, monkeypatch):
    def skip(*a, **k):
        return ToolReviewResult([], ["LLM step skipped: no key."], False,
                                model="deepseek-flash", status="not_configured",
                                error="no_api_key")

    monkeypatch.setattr(pipeline, "review_with_tools", skip)
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False,
                                 settings=_settings(api_key=""))
    assert report.model_name == "none"
    assert report.llm_status == "not_configured"
    assert report.llm_prompt_tokens == 0
    assert report.evidence_sources == []


def test_disabled_review_names_no_model(demo_repo, monkeypatch):
    def skipped(*a, **k):
        return ToolReviewResult([], ["LLM step skipped: policy."], False,
                                model="deepseek-flash", status="disabled",
                                error="disabled_by_policy")

    monkeypatch.setattr(pipeline, "review_with_tools", skipped)
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False,
                                 settings=_settings(public_mode=True))
    assert report.model_name == "none"
    assert report.llm_status == "disabled_by_public_policy"


# ---- local vs MCP: the same evidence source --------------------------------

def test_local_and_mcp_register_the_same_evidence(repo):
    from pr_guardian.mcp_client import MCPToolBackend

    local = TrustedCodebase.from_git(repo, "main")
    payload = local.read_code_context("src/shipping.py", 1, 8)
    local_registry = EvidenceRegistry(ref=local.snapshot_sha)
    local_registry.record(ToolCallRecord(1, "read_code_context",
                                         {"file_path": "src/shipping.py"},
                                         True, "hit", "", "", 80, 0.01, 0,
                                         "call_read_1"), payload)

    with MCPToolBackend(repo, "HEAD") as backend:
        remote_payload = backend.read_code_context("src/shipping.py", 1, 8)
        remote_sha = backend.snapshot_sha
    remote_registry = EvidenceRegistry(ref=remote_sha)
    remote_registry.record(ToolCallRecord(1, "read_code_context",
                                          {"file_path": "src/shipping.py"},
                                          True, "hit", "", "", 80, 0.01, 0,
                                          "call_read_1"), remote_payload)

    def shape(registry):
        return [(e.file_path, e.start_line, e.end_line, e.ref)
                for e in registry.entries]

    assert shape(local_registry) == shape(remote_registry)
    assert local.snapshot_sha == remote_sha


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PR_GUARDIAN_CACHE_DIR", str(tmp_path / ".cache"))
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "p@example.com")
    _git(root, "config", "user.name", "p")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "shipping.py").write_text(
        '"""Shipping."""\n\n\ndef cost(weight):\n    """Return a cost."""\n'
        "    return round(4.5 + weight, 2)\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args],
                          check=True, capture_output=True, text=True).stdout
