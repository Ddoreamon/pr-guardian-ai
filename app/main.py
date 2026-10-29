"""FastAPI application: the PR Guardian demo web UI and JSON API.

Everything shown on the page comes from running the real review pipeline
(pr_guardian.pipeline.run_review) against the trusted demo repository. There are
no canned findings: with no API key configured the page shows the real Ruff and
pytest results and states plainly that the LLM step did not run.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from pr_guardian.diff_view import get_display_diff, parse_display_diff
from pr_guardian.git_reader import GitReaderError
from pr_guardian.pipeline import run_review
from pr_guardian.settings import get_settings, load_dotenv
from pr_guardian.version import __version__

from .demo_cases import BASE_BRANCH, CASES_BY_ID, DEMO_CASES, demo_repo
from .export import to_json, to_markdown
from .store import InvalidDecision, ReviewNotFound, StoredReview, review_lock, store
from .uploads_api import router as uploads_router

APP_DIR = Path(__file__).resolve().parent

load_dotenv()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Build the trusted demo repository at startup.

    This keeps the first review request fast and surfaces a broken environment
    (e.g. no git binary) immediately in /health and the UI, instead of failing
    on a visitor's first click.
    """
    try:
        demo_repo.path()
    except RuntimeError:
        pass  # recorded on demo_repo.error; reported by /health and the page banner
    yield


app = FastAPI(title="PR Guardian", version=__version__, lifespan=lifespan)
app.include_router(uploads_router)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class ReviewRequestBody(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    run_tests: bool = True


class DecisionBody(BaseModel):
    decision: str = Field(pattern="^(pending|accepted|rejected)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _case_payload() -> list[dict]:
    return [
        {
            "case_id": c.case_id,
            "branch": c.branch,
            "title": c.title,
            "category": c.category,
            "expectation": c.expectation,
            "description": c.description,
        }
        for c in DEMO_CASES
    ]


def _config_payload() -> dict:
    cfg = get_settings()
    return {
        "llm_status": cfg.llm_status(),
        "model": cfg.model if cfg.llm_configured else "",
        "public_mode": cfg.public_mode,
        "test_execution": cfg.allow_test_execution,
        "llm_calls_used": store.llm_calls_used(),
        "llm_call_limit": cfg.max_live_llm_calls,
        "demo_repo_error": demo_repo.error,
    }


def _diff_payload(case_id: str) -> list[dict]:
    case = CASES_BY_ID[case_id]
    repo = demo_repo.path()
    files = parse_display_diff(get_display_diff(repo, BASE_BRANCH, case.branch))
    return [
        {
            "file_path": f.file_path,
            "change_type": f.change_type,
            "hunks": [
                {
                    "header": h.header,
                    "lines": [
                        {"kind": ln.kind, "old_no": ln.old_no, "new_no": ln.new_no, "text": ln.text}
                        for ln in h.lines
                    ],
                }
                for h in f.hunks
            ],
        }
        for f in files
    ]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"cases": _case_payload(), "config": _config_payload(),
                 "version": __version__},
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request) -> HTMLResponse:
    cfg = get_settings()
    from app.uploads_api import _limits, uploads_allowed

    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"version": __version__, "enabled": uploads_allowed(cfg),
                 "limits": _limits(), "config": _config_payload()},
    )


@app.get("/health")
def health() -> JSONResponse:
    cfg = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "llm_status": cfg.llm_status(),
            "public_mode": cfg.public_mode,
            "test_execution": cfg.allow_test_execution,
            "demo_repo_ready": demo_repo.error == "",
            "demo_repo_error": demo_repo.error,
            "cases": len(DEMO_CASES),
        }
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/cases")
def api_cases() -> dict:
    return {"cases": _case_payload(), "config": _config_payload()}


@app.get("/api/cases/{case_id}/diff")
def api_case_diff(case_id: str) -> dict:
    if case_id not in CASES_BY_ID:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    try:
        return {"case_id": case_id, "files": _diff_payload(case_id)}
    except (GitReaderError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"demo repository unavailable: {exc}") from exc


@app.post("/api/reviews")
def api_run_review(body: ReviewRequestBody) -> dict:
    if body.case_id not in CASES_BY_ID:
        raise HTTPException(status_code=404, detail=f"unknown case: {body.case_id}")
    case = CASES_BY_ID[body.case_id]
    cfg = get_settings()

    # Cost protection for a public instance: cap real model calls.
    use_llm = True
    budget_note = ""
    if (
        cfg.live_llm_enabled
        and cfg.public_mode
        and not store.try_consume_llm_budget(cfg.max_live_llm_calls)
    ):
        use_llm = False
        budget_note = (
            f"Live LLM budget exhausted ({cfg.max_live_llm_calls} calls); "
            "showing deterministic results only."
        )

    try:
        with review_lock:
            report = run_review(
                demo_repo.path(),
                BASE_BRANCH,
                case.branch,
                use_llm=use_llm,
                run_tests=body.run_tests,
                settings=cfg,
            )
    except GitReaderError as exc:
        raise HTTPException(status_code=400, detail=f"git error: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # never let an unexpected failure crash the page
        raise HTTPException(status_code=500, detail=f"review failed: {type(exc).__name__}: {exc}") from exc

    if budget_note:
        report.notes.append(budget_note)

    stored = StoredReview(report=report, case_id=case.case_id, case_title=case.title)
    store.add(stored)
    return {
        "review_id": report.review_id,
        "case": {"case_id": case.case_id, "title": case.title, "expectation": case.expectation},
        "report": report.model_dump(mode="json"),
        "approvals": {f.id: stored.approval_of(f.id) for f in report.final_findings},
        "files": _diff_payload(case.case_id),
    }


@app.get("/api/reviews/{review_id}")
def api_get_review(review_id: str) -> dict:
    try:
        stored = store.get(review_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown review: {review_id}") from exc
    return {
        "review_id": review_id,
        "report": stored.report.model_dump(mode="json"),
        "approvals": {f.id: stored.approval_of(f.id) for f in stored.report.final_findings},
    }


@app.post("/api/reviews/{review_id}/findings/{finding_id}/decision")
def api_set_decision(review_id: str, finding_id: str, body: DecisionBody) -> dict:
    try:
        decision = store.set_decision(review_id, finding_id, body.decision)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidDecision as exc:
        raise HTTPException(status_code=400, detail=f"invalid decision: {exc}") from exc
    return {"review_id": review_id, "finding_id": finding_id, "decision": decision}


@app.get("/api/reviews/{review_id}/export")
def api_export(
    review_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
    approved_only: bool = False,
):
    try:
        stored = store.get(review_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown review: {review_id}") from exc

    if format == "json":
        return JSONResponse(
            content=to_json_dict_for_export(stored, approved_only),
            headers={"Content-Disposition": f'attachment; filename="review-{review_id}.json"'},
        )
    return PlainTextResponse(
        to_markdown(stored, approved_only=approved_only),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="review-{review_id}.md"'},
    )


def to_json_dict_for_export(stored: StoredReview, approved_only: bool):
    import json

    return json.loads(to_json(stored, approved_only=approved_only))
