"""Upload review orchestration: validated files in, a ReviewReport out.

The uploaded code is never imported, compiled, executed, tested or installed. It is
written into a fresh temporary directory, analysed by a separate `-I` subprocess with a
hard timeout, and the directory is removed afterwards. The resulting report uses the
same `ReviewReport` model as the demo flow, so retrieval, approval and export work
unchanged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    ChangedFile,
    ChangeType,
    FindingSource,
    ReviewCompleteness,
    ReviewFinding,
    ReviewReport,
    TestSummary,
    VerificationStatus,
)
from .settings import Settings
from .uploads import (
    MAX_ANALYSIS_SECONDS,
    UploadedFile,
    UploadRejected,
    safe_target,
)

WORKER_TIMEOUT_S = MAX_ANALYSIS_SECONDS + 5
MAX_FINDINGS = 500

LIMIT_NOTES = [
    "Uploaded code is never executed, imported, tested or installed.",
    "Ruff reports style and lint problems; it is not a logic or security proof.",
    "Pattern findings (UPxxx) are heuristics marked suspected, not confirmed defects.",
    "Absence of findings does not prove the code is bug-free.",
]

_AI_KIND = "provisional"


def _write_files(root: Path, files: list[UploadedFile]) -> None:
    for item in files:
        target = safe_target(root, item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)


def _run_worker(root: Path) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "pr_guardian.upload_worker", str(root)],
            capture_output=True, text=True, timeout=WORKER_TIMEOUT_S,
            cwd=str(root), env={"PATH": "", "PYTHONPATH": ""}, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UploadRejected(
            "analysis_timeout",
            f"analysis exceeded {MAX_ANALYSIS_SECONDS:.0f}s and was stopped",
        ) from exc
    if proc.returncode != 0:
        raise UploadRejected("analysis_failed",
                             "the analyser could not process this upload")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UploadRejected("analysis_failed",
                             "the analyser returned no usable result") from exc
    if not isinstance(payload, dict) or "files" not in payload:
        raise UploadRejected("analysis_failed", "the analyser returned no files")
    return payload


def _finding(raw: dict, index: int) -> ReviewFinding:
    source = raw.get("source", "heuristic")
    if source == "ruff":
        finding_source, verification = FindingSource.RUFF, VerificationStatus.CONFIRMED
    elif source == "syntax":
        finding_source, verification = FindingSource.SYNTAX, VerificationStatus.SUSPECTED
    else:
        finding_source, verification = (FindingSource.HEURISTIC,
                                        VerificationStatus.SUSPECTED)
    return ReviewFinding(
        id=f"U{index:04d}",
        file_path=str(raw["file_path"]),
        start_line=max(1, int(raw.get("start_line") or 1)),
        end_line=max(1, int(raw.get("end_line") or raw.get("start_line") or 1)),
        category=str(raw.get("category") or "lint"),
        severity=str(raw.get("severity") or "low"),
        summary=str(raw.get("summary") or ""),
        explanation=str(raw.get("explanation") or ""),
        evidence="",
        suggested_fix=str(raw.get("suggested_fix") or ""),
        source=finding_source,
        verification_status=verification,
        rule=(str(raw["rule"]) if raw.get("rule") else None),
        evidence_refs=[],
        evidence_status="not_applicable",
        claim_status="claim_unjudged",
        evidence_validation_notes=[_AI_KIND] if source == "llm" else [],
    )


def _changed_files(files: list[UploadedFile], analysed: dict) -> list[ChangedFile]:
    by_path = {item["path"]: item for item in analysed.get("files", [])}
    out = []
    for item in files:
        lines = int(by_path.get(item.path, {}).get("lines") or 0)
        out.append(ChangedFile(
            file_path=item.path,
            change_type=ChangeType.ADDED,
            added_lines=list(range(1, lines + 1)),
            deleted_lines=[],
            language="python",
        ))
    return out


def run_upload_review(files: list[UploadedFile], settings: Settings,
                      *, store=None) -> ReviewReport:
    """Analyse validated uploads and return a report. Never executes user code."""
    root = Path(tempfile.mkdtemp(prefix="pr-guardian-upload-"))
    try:
        _write_files(root, files)
        analysed = _run_worker(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    raw_findings = analysed.get("findings") or []
    if not isinstance(raw_findings, list):
        raw_findings = []
    findings = [_finding(raw, i) for i, raw in enumerate(raw_findings[:MAX_FINDINGS], 1)]
    findings.sort(key=lambda f: (f.file_path, f.start_line, f.rule or ""))

    notes = list(LIMIT_NOTES)
    if analysed.get("ruff_status") not in ("ok", "empty"):
        notes.append(f"Ruff could not complete ({analysed.get('ruff_status')}); "
                     f"lint coverage is incomplete for this upload.")

    report = ReviewReport(
        review_id="upload-" + uuid.uuid4().hex[:12],
        repository="upload",
        base_ref="",
        head_ref="upload",
        model_name="",
        changed_files=_changed_files(files, analysed),
        static_findings=findings,
        llm_findings=[],
        final_findings=list(findings),
        notes=notes,
        test_summary=TestSummary(
            ran=False, passed=0, failed=0, errors=0, skipped=0,
            skipped_reason="uploads are never executed; no tests were run",
        ),
        llm_status=("disabled_by_request" if not settings.upload_ai_enabled
                    else "not_configured"),
        llm_note="AI review is off by default for uploads.",
        tool_status="disabled",
        tool_calls=[],
        review_status="completed",
        completeness=ReviewCompleteness(
            status="completed",
            reason="upload_analysis_completed",
            findings_signal=("has_findings" if findings
                             else "no_findings_in_completed_review"),
        ),
        created_at=datetime.now(UTC),
    )
    return report


def apply_ai_review(report: ReviewReport, files: list[UploadedFile],
                    settings: Settings, store) -> ReviewReport:
    """Optional single-budget AI pass over the uploaded source.

    Disabled unless `PR_GUARDIAN_UPLOAD_AI=1`, never in public mode. The uploaded text is
    wrapped as untrusted data, every returned finding must cite a real uploaded line,
    and every AI finding stays `suspected`.
    """
    if not settings.upload_ai_enabled:
        return report
    if settings.public_mode:
        return report.model_copy(update={
            "llm_status": "disabled_by_public_policy",
            "llm_note": "uploads never call a paid model in public mode",
        })
    if not settings.llm_configured:
        return report.model_copy(update={"llm_status": "not_configured"})
    if store is not None and not store.try_consume_llm_budget(settings.max_live_llm_calls):
        return report.model_copy(update={"llm_status": "budget_exhausted"})
    try:
        from .upload_ai import ask_for_findings
        ai_findings, note = ask_for_findings(files, report.changed_files, settings)
    except Exception as exc:  # noqa: BLE001 - a failed model call is reported, not hidden
        return report.model_copy(update={
            "llm_status": "failed",
            "llm_note": f"the model call failed: {type(exc).__name__}",
        })
    if not ai_findings:
        return report.model_copy(update={"llm_status": "ok",
                                         "llm_note": note})
    merged = list(report.final_findings) + ai_findings
    return report.model_copy(update={
        "llm_findings": ai_findings,
        "final_findings": merged,
        "llm_status": "ok",
        "llm_note": note,
    })
