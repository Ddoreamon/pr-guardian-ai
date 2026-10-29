# Changelog

All notable changes to PR Guardian. The version below is the single source of truth in
`src/pr_guardian/version.py`; the README, the FastAPI app, `/health`, the page footer
and this file are checked against it by `tests/test_version.py`.

## [1.0.0] — Release Candidate

First product release. Everything below ships with the 1.0 build; experimental work
marked **not shipped** stays in the repository as research, off by default or absent
from the release path.

### Shipped

* **Deterministic evidence**: Ruff and pytest run before any model call; their results
  are real whether or not AI is enabled.
* **Structured AI review**: OpenAI-compatible chat completions (DeepSeek by default),
  JSON payload validated by Pydantic, findings with file, line span and severity.
* **Function calling** (opt-in, `PR_GUARDIAN_TOOL_CALLS_ENABLED=1`): read-only
  `read_code_context`, `find_symbol_definition`, `search_code` over a pinned
  `GitSnapshot`, restricted to `src/**/*.py`.
* **MCP stdio tools** (`PR_GUARDIAN_TOOL_BACKEND=mcp`): the same three tools over a
  local MCP session with a whitelisted child environment; payloads identical to local.
* **BM25 repository retrieval**: deterministic keyword index over `src/**/*.py`,
  cache keyed and verified by commit SHA.
* **Evidence provenance**: a finding's citation counts only when every cited line was
  delivered in full from the reviewed commit; verified findings are `suspected` with
  `claim_unjudged`.
* **Review completeness**: `completed` / `partial` / `failed` / `not_run` / `unknown`,
  with requested, executed, denied and errored call counts kept separate.
* **Human accept/reject** with an audit trail, and **JSON / Markdown export** that
  carries no host paths or credentials.
* **FastAPI web UI** with a tool-trace panel, evidence badges and honest status banners.
* **Version single-sourcing**: package, packaging metadata, app, `/health`, footer,
  README and changelog all derive from `src/pr_guardian/version.py`.

### Not shipped in 1.0

| Work | Status | Why |
|---|---|---|
| Dense retrieval + RRF hybrid | **No-Go** | retrieval quality did not justify the cost; branch `feature/v3-hybrid-rag` is not in the 1.0 line |
| Exact-repeat tool cache | **No-Go** | 0 exact repeats across 37 real recorded tool calls |
| Evidence-first planner prompt | **No-Go** | fewer calls, but a required evidence range was lost — weaker proof |
| Evidence sufficiency gate | research prototype | offline evaluator only; not imported by the app or the pipeline |
| Shadow replay | research tool | offline script; never affects a review |
| Arbitrary-repository review | not exposed | the web build reviews the three preset demo cases only |

### Known limits

* The benchmark figures in the README come from small, self-authored synthetic
  repositories (17 held-out cases); they are not production accuracy.
* The public Render deployment runs with live model calls disabled and may run an
  older build than this repository; the page footer reports the running version.
* Windows is the development platform; CI covers Linux, with browser checks on Linux.

## [1.1.0rc1] — Local release candidate

Upload & review, opt-in and local-only. Not a stable release: the version keeps the
`rc` suffix until the feature leaves release-candidate status.

### Added

* `/upload` page and `/api/uploads` for `.py` files or a ZIP, analysed without execution:
  AST parsing, `UP001`–`UP007` heuristics and Ruff over a temporary copy.
* Bounded analyser concurrency (`PR_GUARDIAN_UPLOAD_CONCURRENCY`, default 2): a busy
  instance answers 503 with `Retry-After` instead of queueing.
* Per-stage analysis status (`ast_status`, `ruff_status`, `worker_status`, `ai_status`).
  Any degraded stage marks the review `partial` and keeps the findings already produced.
* UTF-8 BOM tolerance for Windows-authored files.
* JSON/Markdown export, accept/reject and the existing demo flow are unchanged.

### Security

* Uploaded code is never executed, imported, tested or installed; Ruff runs in a
  separate `-I` process with a timeout and the process tree is killed on timeout.
* Path confinement, Zip Slip and symlink refusal, size/ratio/time limits, case-insensitive
  duplicate detection, UTF-8 only.
* Uploads are **disabled by default** and refused in public mode unless both
  `PR_GUARDIAN_UPLOAD_ENABLED=1` and `PR_GUARDIAN_UPLOAD_PUBLIC=1` are set.

### Known limits

* Ruff findings are lint evidence, not proof of a logic defect; `UPxxx` findings are
  heuristics marked `suspected`.
* No OS-level resource sandbox and no per-IP rate limit: public anonymous upload remains
  **No-Go**.

## Unreleased

### Upload & review (V1.1, default off)

* new `/upload` page and `/api/uploads` endpoint for `.py` files or a ZIP;
* analysis is AST parsing, `UP001`–`UP007` heuristics and Ruff — uploaded code is never
  executed, imported, tested or installed;
* path confinement, Zip Slip and symlink refusal, size/ratio/time limits, case-insensitive
  duplicate detection, UTF-8 only;
* findings reuse `ReviewReport`, so accept/reject and JSON/Markdown export work unchanged;
* optional single-call AI pass behind `PR_GUARDIAN_UPLOAD_AI=1`, never in public mode;
* public enablement remains **No-Go** until the analyser runs under an OS-level resource
  sandbox and a per-IP rate limit exists.

Nothing. V4.6 and any further agent/retrieval work are out of scope for 1.0.
