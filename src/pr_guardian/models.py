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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
