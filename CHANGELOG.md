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

## Unreleased

Nothing. V4.6 and any further agent/retrieval work are out of scope for 1.0.
