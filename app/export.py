"""Export a stored review as Markdown or JSON, including human-approval state."""

from __future__ import annotations

import json
from typing import Any

from pr_guardian.models import ReviewFinding

from .store import StoredReview

_DECISION_MARK = {"pending": "—", "accepted": "ACCEPTED", "rejected": "REJECTED"}


def _finding_block(f: ReviewFinding, decision: str) -> list[str]:
    source_line = (
        f"- **Source:** {f.source.value} · **Status:** {f.verification_status.value} "
        f"· **Severity:** {f.severity}"
    )
    return [
        f"### {f.summary}",
        f"- **Location:** `{f.file_path}:{f.start_line}`",
        source_line,
        f"- **Human decision:** {_DECISION_MARK.get(decision, decision)}",
        f"- **Evidence:** {f.evidence or '—'}",
        f"- **Explanation:** {f.explanation or '—'}",
        f"- **Suggested fix:** {f.suggested_fix or '—'}",
        "",
    ]


def to_markdown(review: StoredReview, approved_only: bool = False) -> str:
    report = review.report
    findings = report.final_findings
    if approved_only:
        findings = [f for f in findings if review.approval_of(f.id) == "accepted"]

    lines = [
        "# PR Guardian review",
        "",
        f"- **Case:** {review.case_title or review.case_id or '—'}",
        f"- **Repository:** {report.repository}",
        f"- **Base / head:** `{report.base_ref}` → `{report.head_ref}`",
        f"- **Review id:** `{report.review_id}`",
        f"- **Created:** {report.created_at.isoformat()}",
        f"- **LLM status:** {report.llm_status}"
        + (f" ({report.llm_note})" if report.llm_note else ""),
        "",
        "## Test evidence",
        "",
    ]
    ts = report.test_summary
    if ts.ran:
        lines.append(
            f"- pytest: {ts.passed} passed, {ts.failed} failed, {ts.errors} errors "
            f"({ts.duration_seconds:.2f}s)"
        )
        if ts.failed_tests:
            lines.append("- Failing tests: " + ", ".join(f"`{t}`" for t in ts.failed_tests))
    else:
        lines.append(f"- pytest not run: {ts.skipped_reason or 'n/a'}")

    lines += ["", "## Changed files", ""]
    lines += [f"- `{f.file_path}` ({f.change_type.value})" for f in report.changed_files] or ["- (none)"]

    lines += ["", "## Findings", ""]
    if findings:
        for f in findings:
            lines += _finding_block(f, review.approval_of(f.id))
    else:
        lines.append("No reportable findings.")

    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
    return "\n".join(lines) + "\n"


def to_json_dict(review: StoredReview, approved_only: bool = False) -> dict[str, Any]:
    report = review.report
    findings = report.final_findings
    if approved_only:
        findings = [f for f in findings if review.approval_of(f.id) == "accepted"]
    payload = json.loads(report.model_dump_json())
    payload["case"] = {"id": review.case_id, "title": review.case_title}
    payload["findings"] = [
        {**json.loads(f.model_dump_json()), "human_decision": review.approval_of(f.id)}
        for f in findings
    ]
    payload["export_approved_only"] = approved_only
    return payload


def to_json(review: StoredReview, approved_only: bool = False) -> str:
    return json.dumps(to_json_dict(review, approved_only), indent=2, ensure_ascii=False)
