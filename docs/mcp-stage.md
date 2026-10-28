# MCP stage — engineering wrap-up and real end-to-end verification

Branch `feature/v2-mcp-server`. Nothing here changes `main`, the Render service or
the frozen benchmark, and no MCP endpoint is exposed publicly.

MCP is a **transport**, not a smarter reviewer: the frozen held-out numbers in
`docs/v2-heldout-independent-audit.md` are unchanged and were not re-run.

---

## 1. Review fixes

### 1.1 The server now enforces its own schema

The low-level MCP `Server` does not validate `input_schema`, so `additionalProperties:
false` was decorative. `validate_arguments()` in `mcp_server.py` now rejects:

| Case | Error code |
|---|---|
| undeclared field (`repo`, `ref`, `sha`, `extra`, …) | `unknown_parameter` (+ the declared list) |
| missing required field | `missing_parameter` |
| wrong type, including `bool` passed as `int` | `bad_type` |
| bad line range / too many lines | `bad_range` |
| malformed symbol name | `bad_symbol` |
| path outside `src/**/*.py`, traversal, absolute, non-`.py` | `denied_path` / `unsupported_type` |
| tool that does not exist (e.g. `run_shell`) | `unknown_tool` |

The repository and commit remain bound at process start and are still not
parameters; a caller that passes them gets `unknown_parameter` instead of a
silently ignored field.

### 1.2 Latency no longer leaks into model-visible output

`MCPToolBackend` used to add `mcp_latency_s` to every payload, which the tool loop
then forwarded to the model — making Local and MCP text differ for no reason.
Latency now lives in `backend.audit` (`ToolRPC` records, `audit_summary()`); the
payload is byte-identical to the in-process result, and a test asserts it.

### 1.3 Session lifecycle

| Path | Behaviour | Test |
|---|---|---|
| never becomes ready | `TimeoutError`, private loop hard-stopped (pending tasks cancelled), thread reaped | `test_start_timeout_raises_and_reaps_the_thread` |
| server exits early | `RuntimeError("MCP session failed: …")`, thread reaped | `test_server_exiting_early_raises_runtime_error` |
| RPC blocks | `TimeoutError`, pending coroutine cancelled, session still closable | `test_rpc_timeout_raises_and_still_closes` |
| `close()` twice / after an exception | no-op, no raise, thread gone | `test_close_is_idempotent_and_safe_after_errors` |
| leaked helper thread | none after `close()` | `test_no_helper_thread_is_left_running` |
| leaked server process | none after `close()` (psutil child scan) | `test_no_server_process_is_left_running` |

Found while writing these: `__init__` had lost its `self.start()` call in an
earlier rewrite, so the backend was never ready — the startup-failure tests caught
it. That is precisely the class of bug this round was meant to surface.

### 1.4 `is_error` is authoritative

`decode_result()` now rewrites any result with `is_error=True` to `ok: false`,
keeping a structured error when present and synthesising `transport_error`
otherwise. A protocol error can no longer be counted as a successful tool call
even if the payload claims `ok: true`; malformed JSON also becomes an error
payload rather than an empty success.

### 1.5 README

Held-out is recorded as **complete and frozen** with decided *and* strict figures,
and the MCP section states explicitly that it does not change detection accuracy.

---

## 2. Real DeepSeek → MCP → Finding chain

Purpose-built development cases (not the benchmark):

* **A (bug, cross-file)** — `total()` calls `shipping.estimate(weight_kg, express=True)`
  while `estimate` takes only `weight_kg`, so the call raises `TypeError`. Only
  visible by reading another file.
* **B (clean, cross-file)** — `describe()` passes `currency="USD"` to
  `format_money`, which already accepts it. Looks like a change, is not a defect.

Run: `python scripts/mcp_smoke.py --out docs/mcp-smoke` with
`PR_GUARDIAN_TOOL_CALLS_ENABLED=1`, `PR_GUARDIAN_TOOL_BACKEND=mcp`,
`PR_GUARDIAN_PUBLIC_MODE=0`, real `deepseek-flash`. No mocks, no recorded
transcripts.

### 2.1 The chain, as observed

| Requirement | Evidence |
|---|---|
| real `tool_calls` from the provider | 3 calls in run 1, each with a provider `tool_call_id` |
| MCP client → server over stdio | `tool_status: ok`, payloads returned by the child process |
| server reads the pinned SHA | every payload `ref` == `8e45fc1ddc12…` (the branch tip), worktree edits ignored |
| results returned per `tool_call_id` | trace records `call_00_ET_dBh8…`, `call_01_ET_OhUZ…`, `call_00_bUOI…` |
| structured finding | `total() passes an unsupported express=True keyword to shipping.estimate()` — `src/checkout.py:10`, severity high |
| validator | 1 LLM finding → 1 final finding (kept, `suspected`) |
| tool trace matches the calls | 3 recorded calls == 3 model calls' requests |
| session closed | `session_closed: true`, no leftover thread |

Full trace: [`docs/mcp-smoke/REPORT.md`](mcp-smoke/REPORT.md), raw
[`results.json`](mcp-smoke/results.json).

---

## 3. Local vs MCP (same snapshot, same arguments)

| | MCP | in-process |
|---|---|---|
| session start | 0.965 s | n/a |
| per-call median | 0.053 s | 0.050 s |
| payloads identical | **4/4** | — |
| payload `ref` == pinned SHA | **4/4** | — |
| session closed / leaked threads | yes / none | — |

End-to-end (real model, two dev cases):

| Case | Backend | Model calls | Tool calls | Prompt tokens | Findings → final | Status | Wall |
|---|---|---|---|---|---|---|---|
| bug | mcp | 3 | 3 | 4,772 | 1 → 1 | ok | 7.30 s |
| bug | local | 3 | 3 | 4,826 | 1 → 1 | ok | 5.62 s |
| clean | mcp | 2 | 2 | 2,645 | 0 → 0 | ok | 3.75 s |
| clean | local | 2 | 2 | 2,645 | 0 → 0 | ok | 2.01 s |

Both backends found the bug and stayed silent on the clean case. The tool
sequences are the same shape; the small differences (read range 30 vs 20 lines,
token counts) come from the model choosing different ranges, **not** from the
transport. These are single runs: they are a protocol check, not a detection
benchmark, and must not be read as accuracy evidence.

---

## 4. What is still open

* Wall-clock for MCP runs is ~1.3–1.9 s higher, dominated by the one-off session
  start; a per-review server is fine, a per-call one would not be.
* The MCP backend is synchronous-by-thread. Concurrent reviews would each get
  their own session and thread; there is no pooling.
* stdio only — no streamable HTTP, no auth, no multi-client fan-out.
* `psutil` is used only by tests to prove process cleanup; it is a dev dependency.
* Token accounting is now on the report (`llm_prompt_tokens`,
  `llm_completion_tokens`) but there is still no cost estimation (no price table).
