# PR Guardian 1.1.0rc1 — local release candidate

Status: **local release candidate**, not a stable release and not deployed. The public
Render instance runs 1.0 and has no upload endpoint.

## What this candidate is

Deterministic lint plus a small, explainable set of risk patterns over code you upload,
with every finding located and its provenance labelled. It never executes what you upload.

## Run it

```bash
python -m venv .venv && . .venv/Scripts/activate     # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# uploads stay off unless you opt in
PR_GUARDIAN_UPLOAD_ENABLED=1 uvicorn app.main:app --port 8000
```

* `http://127.0.0.1:8000/` — the three preset demos, unchanged.
* `http://127.0.0.1:8000/upload` — upload `.py` files or one `.zip`.

Without the variable the upload page exists but reports that analysis is disabled; the
demo flow is unaffected.

## Optional AI pass

Off by default. `PR_GUARDIAN_UPLOAD_AI=1` with a server-side key allows a single budgeted
call; in public mode it is refused regardless, and every AI finding stays `suspected`.

## Limits (enforced server-side)

| Limit | Value |
|---|---|
| files per upload | 25 |
| bytes per file | 256 KiB |
| uncompressed total | 4 MiB |
| archive size | 8 MiB |
| compression ratio | 100 |
| analysis time | 20 s (+5 s grace) |
| concurrent analyses | 2 (503 + `Retry-After` above that) |
| findings per report | 500 |

## What the report tells you

* `AST ok/partial/failed`, `Ruff ok/empty/timeout/unavailable/exit_error/unparsable`,
  `worker ok/timeout/error`, `AI not_run/ok/failed/...`.
* Any degraded stage → `review_status = partial`, findings kept, completeness reason
  `partial_analysis`. A failed lint is never shown as a clean review.
* Ruff findings are `confirmed` *as rule hits*; `UP001`–`UP007` are `suspected` heuristics.
* No findings shows: *"No issues detected by the enabled checks. This does not prove the
  code is bug-free."*

## Acceptance evidence (this candidate)

Three self-authored MIT projects, run through the real API and (case A) the real browser:

| case | files | LOC | findings | valid | duplicate | false positive | status |
|---|---|---|---|---|---|---|---|
| A csv_report | 1 | 35 | 4 | 3 | 1 | 0 | completed |
| B fastapi service | 1 | 36 | 5 | 4 | 1 | 0 | completed |
| C inventory package (zip) | 4 | 55 | 5 | 3 | 1 | 1 | completed |

Fix → re-upload: A loses F401/F541/UP006/B006 and has **no** findings afterwards; B loses
F401/UP005/E722 and keeps **BLE001** (a real defect in code we did not touch); C loses
UP002/UP006 and keeps **RUF100** (the deliberate `# noqa`, which is the recorded false
positive). Missed problems are listed per case in `docs/v1.1-acceptance/PRODUCT_REVIEW.md`
and were not removed.

## Verification performed on this candidate

Full suite 471 tests (1 skipped on Windows for a POSIX-only test), Ruff clean, API/MCP 67,
browser 77/77, upload acceptance 11/11. Default-off and public-mode rejection are covered
by tests (`tests/test_uploads.py`, `tests/test_upload_hardening.py`).

## Go / No-Go

| Context | Decision |
|---|---|
| Local RC | **Go.** Opt-in, defaults off, honest status, bounded resources, no execution. |
| Self-hosted, trusted users | **Go with care.** Same code path; put it behind your own auth/rate limit if the users are not trusted. |
| Public anonymous upload | **No-Go.** No OS-level resource sandbox, no per-IP rate limit, no reverse-proxy body limit, no worker RSS cap. |

Local RC passing does **not** mean public upload is safe: the difference is abuse and
isolation, not correctness.
