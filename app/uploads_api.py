"""HTTP surface for untrusted uploads.

Off unless `PR_GUARDIAN_UPLOAD_ENABLED=1`; on a public instance it additionally needs
`PR_GUARDIAN_UPLOAD_PUBLIC=1`. Uploads are analysed in a subprocess and never executed.
"""

from __future__ import annotations

import threading
import uuid

import anyio
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from pr_guardian.settings import Settings, get_settings
from pr_guardian.upload_review import apply_ai_review, run_upload_review
from pr_guardian.uploads import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    MAX_ZIP_BYTES,
    UploadRejected,
    collect_from_files,
    collect_from_zip,
)

from .store import StoredReview, store

router = APIRouter()

# Bounded analyser concurrency: a slot is taken before a worker starts, so an
# unbounded number of uploads can never spawn an unbounded number of processes.
_slots_lock = threading.Lock()
_slots_in_use = 0


def try_acquire_slot(limit: int) -> bool:
    global _slots_in_use
    with _slots_lock:
        if _slots_in_use >= max(1, limit):
            return False
        _slots_in_use += 1
        return True


def release_slot() -> None:
    global _slots_in_use
    with _slots_lock:
        _slots_in_use = max(0, _slots_in_use - 1)


def active_slots() -> int:
    with _slots_lock:
        return _slots_in_use

MAX_ARCHIVE_UPLOAD_BYTES = MAX_ZIP_BYTES
MAX_REQUEST_BYTES = MAX_TOTAL_BYTES + MAX_ARCHIVE_UPLOAD_BYTES + 1024 * 1024


def uploads_allowed(cfg: Settings) -> bool:
    """Uploads are local-only: a public instance refuses them regardless of flags."""
    if cfg.public_mode:
        return False
    return cfg.upload_enabled


def _limits() -> dict:
    return {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES, "max_zip_bytes": MAX_ZIP_BYTES,
            "max_request_bytes": MAX_REQUEST_BYTES}


@router.get("/api/uploads/config")
def upload_config() -> dict:
    cfg = get_settings()
    return {
        "enabled": uploads_allowed(cfg),
        "public_mode": cfg.public_mode,
        "ai_enabled": bool(cfg.upload_ai_enabled and not cfg.public_mode
                           and cfg.llm_configured),
        "limits": _limits(),
    }


async def _read_limited(upload: UploadFile, cap: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > cap:
            raise UploadRejected("too_large", f"{upload.filename or 'file'} is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _rejected(exc: UploadRejected) -> JSONResponse:
    status = 413 if exc.code in {"too_large", "file_too_large", "upload_too_large",
                                 "archive_too_large"} else 400
    return JSONResponse(status_code=status,
                        content={"error": exc.code, "message": exc.message})


def _busy_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "2"},
        content={"error": "busy",
                 "message": "The analyser is busy. Retry in a moment.",
                 "retry_after_s": 2},
    )


@router.post("/api/uploads")
async def create_upload(
    files: list[UploadFile] = File(default=[]),  # noqa: B008 - FastAPI dependency
    archive: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI dependency
) -> JSONResponse:
    cfg = get_settings()
    if not uploads_allowed(cfg):
        return JSONResponse(status_code=403, content={
            "error": "uploads_disabled",
            "message": "Upload analysis is disabled on this instance.",
        })
    if not try_acquire_slot(cfg.upload_concurrency):
        return _busy_response()
    try:
        if archive is not None and archive.filename:
            if files:
                raise UploadRejected("bad_request",
                                     "send either .py files or one .zip, not both")
            data = await _read_limited(archive, MAX_ARCHIVE_UPLOAD_BYTES)
            uploaded = collect_from_zip(data)
        else:
            if not files:
                raise UploadRejected("empty_upload", "no files were uploaded")
            pairs: list[tuple[str, bytes]] = []
            budget = MAX_TOTAL_BYTES
            for item in files:
                name = item.filename or "unnamed.py"
                data = await _read_limited(item, min(MAX_FILE_BYTES, budget))
                budget -= len(data)
                if budget < 0:
                    raise UploadRejected("upload_too_large", "the upload is too large")
                pairs.append((name, data))
            uploaded = collect_from_files(pairs)

        report = await anyio.to_thread.run_sync(
            lambda: run_upload_review(uploaded, cfg, store=store))
        if cfg.upload_ai_enabled:
            report = await anyio.to_thread.run_sync(
                lambda: apply_ai_review(report, uploaded, cfg, store))
        store.add(StoredReview(report=report, case_id="upload",
                               case_title=f"Uploaded files ({len(uploaded)})"))
    except UploadRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001 - never leak internals to the client
        return JSONResponse(status_code=500, content={
            "error": "analysis_failed",
            "message": "The upload could not be analysed. Nothing was executed.",
        })
    finally:
        release_slot()

    return JSONResponse(status_code=201, content={
        "review_id": report.review_id,
        "files": len(report.changed_files),
        "findings": len(report.final_findings),
        "report": report.model_dump(mode="json"),
    })


@router.get("/api/uploads/{review_id}")
def get_upload(review_id: str) -> dict:
    cfg = get_settings()
    if not uploads_allowed(cfg):
        raise HTTPException(status_code=403, detail="Upload analysis is disabled")
    try:
        stored = store.get(review_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown upload review") from exc
    return {"review_id": review_id, "report": stored.report.model_dump(mode="json"),
            "approvals": {f.id: stored.approval_of(f.id)
                          for f in stored.report.final_findings}}


def new_upload_id() -> str:
    return "upload-" + uuid.uuid4().hex[:12]
