"""Render a ReviewReport as Markdown or JSON."""

from __future__ import annotations

from .models import ReviewFinding, ReviewReport


def to_json(report: ReviewReport) -> str:
    return report.model_dump_json(indent=2)


def _finding_md(f: ReviewFinding) -> str:
    return (
        f"### {f.summary}\n"
        f"- **Location:** `{f.file_path}:{f.start_line}`\n"
        f"- **Source:** {f.source.value} | **Status:** {f.verification_status.value} "
        f"| **Severity:** {f.severity}\n"
        f"- **Evidence:** {f.evidence or '—'}\n"
        f"- **Explanation:** {f.explanation or '—'}\n"
        f"- **Suggested fix:** {f.suggested_fix or '—'}\n"
    )


def to_markdown(report: ReviewReport) -> str:
    lines = [
        f"# PR Guardian Review: {report.repository}",
        "",
        f"- Base: `{report.base_ref}`  Head: `{report.head_ref}`",
        f"- Model: `{report.model_name}`",
        f"- Created: {report.created_at.isoformat()}",
        "",
        "## Changed files",
    ]
    lines += [f"- `{f.file_path}` ({f.change_type.value})" for f in report.changed_files] or ["- (none)"]
    lines += ["", "## Findings"]
    if report.final_findings:
        lines += [_finding_md(f) for f in report.final_findings]
    else:
        lines.append("No reportable findings.")
    if report.notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in report.notes]
    return "\n".join(lines) + "\n"
