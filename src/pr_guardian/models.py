"""Pydantic data models shared across the review pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class FindingSource(str, Enum):
    RUFF = "ruff"
    PYTEST = "pytest"
    LLM = "llm"


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"  # backed by deterministic evidence (ruff / pytest)
    SUSPECTED = "suspected"  # LLM hypothesis, not verified
    INVALID = "invalid"      # failed validation, excluded from the report


class ChangedFile(BaseModel):
    file_path: str
    change_type: ChangeType
    added_lines: list[int] = Field(default_factory=list)
    deleted_lines: list[int] = Field(default_factory=list)
    language: str = "unknown"


class EvidenceRef(BaseModel):
    """A citation the model claims to have based a finding on.

    Every field is checked against the evidence registry before it can be shown as
    verified. `evidence_status` is set by the validator, never by the model.
    """

    ref: str = ""                 # commit SHA the model says it read
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    tool_call_id: str = ""
    tool_name: str = ""
    evidence_status: str = "unverified"
    snippet_sha256: str = ""


class ReviewFinding(BaseModel):
    id: str
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    category: str
    severity: str  # low | medium | high
    summary: str
    explanation: str = ""
    evidence: str = ""
    suggested_fix: str = ""
    source: FindingSource
    verification_status: VerificationStatus
    rule: str | None = None  # e.g. Ruff rule code
    # V4.1: provenance. Defaults keep older reports and frozen JSON parseable.
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    # deterministic | source_verified | source_unverified | no_evidence_ref | not_evaluated
    evidence_status: str = "not_evaluated"
    # claim_unjudged (LLM) | deterministic (ruff / pytest)
    claim_status: str = "claim_unjudged"
    evidence_validation_notes: list[str] = Field(default_factory=list)


class TestSummary(BaseModel):
    """Outcome of running the trusted test suite on the head revision."""

    ran: bool = False
    skipped_reason: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failed_tests: list[str] = Field(default_factory=list)
    timed_out: bool = False
    duration_seconds: float = 0.0


class ToolCallRecordModel(BaseModel):
    """Audit record of one tool call the model requested."""

    round: int
    name: str
    arguments: dict = Field(default_factory=dict)
    ok: bool
    # hit | miss | error | budget_denied — "hit" means usable context was retrieved
    outcome: str = "hit"
    error_code: str = ""
    error_message: str = ""
    result_chars: int = 0
    latency_s: float = 0.0
    matched: int = 0
    call_id: str = ""


class ReviewCompleteness(BaseModel):
    """Whether the review actually finished, derived from execution records.

    This is a *process* state, not a defect verdict: `completed` means the flow ran
    to its configured end, never that the code was proven correct.
    """

    # completed | partial | failed | not_run
    status: str = "completed"
    reason: str = ""
    tool_rounds_used: int = 0
    tool_rounds_max: int = 0
    tool_calls_used: int = 0
    tool_calls_max: int = 0
    tool_calls_denied: int = 0
    denied_requests: list[dict] = Field(default_factory=list)
    model_requests: int = 0
    model_failures: int = 0
    tool_errors: int = 0
    tool_misses: int = 0
    duplicate_calls: int = 0
    # not_evaluated | no_findings_in_completed_review
    findings_signal: str = "not_evaluated"


class ReviewReport(BaseModel):
    review_id: str
    repository: str
    base_ref: str
    head_ref: str
    model_name: str
    changed_files: list[ChangedFile] = Field(default_factory=list)
    static_findings: list[ReviewFinding] = Field(default_factory=list)
    llm_findings: list[ReviewFinding] = Field(default_factory=list)
    final_findings: list[ReviewFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    test_summary: TestSummary = Field(default_factory=TestSummary)
    # enabled | ok | failed | not_configured | disabled_by_public_policy | disabled_by_request
    llm_status: str = "not_configured"
    llm_note: str = ""
    # V2: tool calling is opt-in; these stay empty/disabled when it is off.
    tool_status: str = "disabled"
    tool_calls: list[ToolCallRecordModel] = Field(default_factory=list)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    tool_model_calls: int = 0
    tool_model_failures: int = 0
    tool_rounds: int = 0
    # V4.1 provenance roll-up
    evidence_verified: int = 0
    evidence_unverified: int = 0
    trusted_findings: list[ReviewFinding] = Field(default_factory=list)
    evidence_sources: list[dict] = Field(default_factory=list)
    evidence_registry_fingerprint: str = ""
    # V4.2: process state, separate from any defect verdict
    review_status: str = "completed"
    completeness: ReviewCompleteness = Field(default_factory=ReviewCompleteness)
    # explicit groups so a UI cannot confuse provenance with confirmation
    deterministic_findings: list[ReviewFinding] = Field(default_factory=list)
    source_verified_findings: list[ReviewFinding] = Field(default_factory=list)
    source_unverified_findings: list[ReviewFinding] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
