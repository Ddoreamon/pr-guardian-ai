"""Finding validator: the core trust layer.

LLM findings are kept as `suspected` only if they point at a changed file and a
line that was actually added/modified in the diff. Duplicates are merged.
Static (Ruff) findings are kept as `confirmed` because they come from a
deterministic tool, but are still filtered to changed files/lines.
"""

from __future__ import annotations

from .models import ChangedFile, FindingSource, ReviewFinding, VerificationStatus

ALLOWED_SEVERITIES = {"low", "medium", "high"}
_FORBIDDEN_MARKERS = ("rm -rf", "curl ", "wget ", "os.system", "subprocess")


def _changed_lines_index(files: list[ChangedFile]) -> dict[str, set[int]]:
    return {f.file_path: set(f.added_lines) for f in files}


def validate_findings(
    findings: list[ReviewFinding], files: list[ChangedFile]
) -> list[ReviewFinding]:
    index = _changed_lines_index(files)
    kept: list[ReviewFinding] = []
    seen: set[tuple[str, int, str]] = set()

    for f in findings:
        if f.severity not in ALLOWED_SEVERITIES:
            f = f.model_copy(update={"verification_status": VerificationStatus.INVALID})
        # Location check applies to diff-anchored findings (ruff, llm). Pytest findings
        # are suite-level and not tied to a diff line, so they are exempt.
        if f.source != FindingSource.PYTEST:
            if f.file_path not in index:
                f = f.model_copy(update={"verification_status": VerificationStatus.INVALID})
            elif not any(line in index[f.file_path] for line in range(f.start_line, f.end_line + 1)):
                # location is not inside an added/modified line of the diff
                f = f.model_copy(update={"verification_status": VerificationStatus.INVALID})

        if f.source == FindingSource.LLM:
            # never let an LLM hypothesis claim verification
            if f.verification_status == VerificationStatus.CONFIRMED:
                f = f.model_copy(update={"verification_status": VerificationStatus.SUSPECTED})
            text = f"{f.summary} {f.suggested_fix}".lower()
            if any(m in text for m in _FORBIDDEN_MARKERS):
                f = f.model_copy(update={"verification_status": VerificationStatus.INVALID})

        if f.verification_status == VerificationStatus.INVALID:
            kept.append(f)  # kept for transparency; excluded from final_findings
            continue

        key = (f.file_path, f.start_line, f.category)
        if key in seen:
            continue
        seen.add(key)
        kept.append(f)
    return kept


def final_findings(validated: list[ReviewFinding]) -> list[ReviewFinding]:
    return [f for f in validated if f.verification_status != VerificationStatus.INVALID]
