"""Upload review orchestration: validated files in, a ReviewReport out.

The uploaded code is never imported, compiled, executed, tested or installed. It is
written into a fresh temporary directory, analysed by a separate `-I` subprocess with a
hard timeout, and the directory is removed afterwards. The resulting report uses the
same `ReviewReport` model as the demo flow, so retrieval, approval and export work
unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
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
    UploadAnalysis,
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
        # write the normalised text (BOM stripped) so the analyser sees valid UTF-8
        target.write_bytes(item.text.encode("utf-8"))


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the worker and any child it spawned (Ruff), on both platforms.

    POSIX: the worker is started in its own session, so killing the group reaches Ruff.
    Windows: `taskkill /T` walks the child tree; a plain `proc.kill()` would leave Ruff
    running until its own timeout.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False, text=True)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _run_worker(root: Path) -> tuple[dict, str]:
    """Run the analyser with a hard timeout. Returns (payload, worker_status)."""
    proc = subprocess.Popen(
        [sys.executable, "-I", "-m", "pr_guardian.upload_worker", str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(root), env={"PATH": "", "PYTHONPATH": ""},
        **_popen_kwargs(),
    )
    try:
        stdout, _stderr = proc.communicate(timeout=WORKER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise UploadRejected(
            "analysis_timeout",
            f"analysis exceeded {MAX_ANALYSIS_SECONDS:.0f}s and was stopped",
        ) from None
    finally:
        kill_process_tree(proc)

    if proc.returncode != 0:
        raise UploadRejected("analysis_failed",
                             "the analyser could not process this upload")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise UploadRejected("analysis_failed",
                             "the analyser returned no usable result") from exc
    if not isinstance(payload, dict) or "files" not in payload:
        raise UploadRejected("analysis_failed", "the analyser returned no files")
    return payload, "ok"


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
        analysed, worker_status = _run_worker(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    raw_findings = analysed.get("findings") or []
    if not isinstance(raw_findings, list):
        raw_findings = []
    findings = [_finding(raw, i) for i, raw in enumerate(raw_findings[:MAX_FINDINGS], 1)]
    findings.sort(key=lambda f: (f.file_path, f.start_line, f.rule or ""))

    notes = list(LIMIT_NOTES)
    ruff_status = str(analysed.get("ruff_status") or "not_run")
    ast_status = str(analysed.get("ast_status") or "not_run")
    if ruff_status not in ("ok", "empty"):
        notes.append(f"Ruff could not complete ({ruff_status}); "
                     f"lint coverage is incomplete for this upload.")
    if ast_status == "partial":
        notes.append("Some files could not be parsed; those files were only checked "
                     "for syntax, not linted or pattern-scanned.")
    elif ast_status == "failed":
        notes.append("No file could be parsed, so no lint or pattern check ran.")

    degraded = ruff_status not in ("ok", "empty") or ast_status in ("partial", "failed")

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
        review_status="partial" if degraded else "completed",
        completeness=ReviewCompleteness(
            status="partial" if degraded else "completed",
            reason=("partial_analysis" if degraded else "upload_analysis_completed"),
            findings_signal=("has_findings" if findings
                             else ("not_evaluated" if degraded
                                   else "no_findings_in_completed_review")),
        ),
        upload_analysis=UploadAnalysis(
            ast_status=ast_status,
            ruff_status=ruff_status,
            worker_status=worker_status,
            ai_status="not_run",
            files_parsed=int(analysed.get("files_parsed") or 0),
            files_with_syntax_errors=int(analysed.get("files_with_syntax_errors") or 0),
            detail=",".join(notes[4:]),
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
