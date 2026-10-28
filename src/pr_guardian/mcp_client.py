"""MCP client helpers: a real stdio session, plus a sync facade for the agent.

`open_client` speaks the actual protocol to `pr_guardian.mcp_server` over stdio —
there is no in-process shortcut, so the tests exercise serialization, framing and
the server's own dispatch.

`MCPToolBackend` exposes the same two methods as `TrustedCodebase`, so the
function-calling loop can use MCP without knowing about asyncio or transports.
Performance metadata is kept in `backend.audit`; it is **never** added to the tool
payload, so the text the model receives is identical to the in-process tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import mcp
from mcp import StdioServerParameters, types

SERVER_MODULE = "pr_guardian.mcp_server"

LOGGER = logging.getLogger("pr_guardian.mcp_client")


# The server reads git snapshots; it never talks to a model provider. Only the
# variables a Python process and `git` genuinely need are forwarded, so a key in
# the parent environment is never handed to the child.
SAFE_ENV_KEYS = (
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
    "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONHASHSEED",
)


def minimal_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Whitelisted environment for the server process (no API keys)."""
    source = os.environ if base is None else base
    env = {key: source[key] for key in SAFE_ENV_KEYS if key in source}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def server_parameters(repo: Path | str, ref: str,
                      python: str | None = None) -> StdioServerParameters:
    """Command that starts the MCP server for one repository and commit."""
    return StdioServerParameters(
        command=python or sys.executable,
        args=["-m", SERVER_MODULE, "--repo", str(repo), "--ref", ref],
        env=minimal_env(),
        cwd=None,
        encoding="utf-8",
        encoding_error_handler="replace",
    )


@asynccontextmanager
async def open_client(repo: Path | str, ref: str,
                      python: str | None = None) -> AsyncIterator[mcp.Client]:
    """Open a real MCP session over stdio."""
    async with mcp.Client(server_parameters(repo, ref, python)) as client:
        yield client


async def list_tool_names(client: mcp.Client) -> list[str]:
    result = await client.list_tools()
    return [tool.name for tool in result.tools]


def decode_result(result: types.CallToolResult) -> tuple[dict, bool]:
    """Return (payload, is_error).

    `is_error=True` is authoritative: the payload is rewritten to `ok: false`
    even if the server returned `ok: true`, so a protocol-level error can never
    be counted as a successful tool call.
    """
    is_error = bool(getattr(result, "is_error", False))
    text = ""
    for block in result.content or []:
        if getattr(block, "type", "") == "text":
            text = block.text
            break
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"ok": False, "error": {"code": "bad_payload", "message": text[:200]}}
    if not isinstance(payload, dict):
        payload = {"ok": False,
                   "error": {"code": "bad_payload",
                             "message": "tool content was not a JSON object"}}
    if is_error and payload.get("ok") is not False:
        error = payload.get("error")
        if not isinstance(error, dict):
            error = {"code": "transport_error",
                     "message": "the tool reported is_error without a structured error"}
        payload = {"ok": False, "error": error}
    return payload, is_error


async def call_tool_json(client: mcp.Client, name: str,
                         arguments: dict) -> tuple[dict, bool]:
    result = await client.call_tool(name, arguments)
    return decode_result(result)


@dataclass
class ToolRPC:
    """Audit record for one MCP round trip (never sent to the model)."""

    tool: str
    arguments: dict
    latency_s: float
    is_error: bool
    result_chars: int

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "latency_s": self.latency_s,
            "is_error": self.is_error,
            "result_chars": self.result_chars,
        }


class MCPToolBackend:
    """Synchronous facade over one long-lived MCP session.

    The agent loop is synchronous and the MCP client is async. Spawning a server
    process per tool call would distort latency, so the session lives in a
    dedicated thread with its own event loop and calls are submitted to it.

    Lifecycle guarantees:
      * `start()` raises TimeoutError and reaps the thread if the session is not
        ready in time;
      * a server that dies early surfaces as RuntimeError from `start()`;
      * an RPC that blocks raises TimeoutError after `timeout`, and the pending
        coroutine is cancelled;
      * `close()` is idempotent, safe after an exception, and leaves no live
        thread behind.
    """

    def __init__(self, repo: Path | str, ref: str, *, timeout: float = 60.0,
                 python: str | None = None, autostart: bool = True) -> None:
        self.timeout = timeout
        self.audit: list[ToolRPC] = []
        self._params = server_parameters(repo, ref, python)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._last_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._client: mcp.Client | None = None
        self._error: BaseException | None = None
        self._closed = False
        if autostart:
            self.start()

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="mcp-client",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout):
            self._shutdown_thread(hard=True)
            raise TimeoutError(f"MCP session did not become ready within {self.timeout}s")
        if self._error is not None:
            self._shutdown_thread(hard=True)
            raise RuntimeError(f"MCP session failed: {self._error}") from self._error

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._session())
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            self._error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # teardown is best effort
                LOGGER.debug("async generator shutdown failed", exc_info=True)
            loop.close()

    async def _session(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with mcp.Client(self._params) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        finally:
            self._client = None

    def _shutdown_thread(self, *, hard: bool = False) -> None:
        """Stop the session. `hard` also cancels pending tasks.

        The normal path sets the stop event and lets the session close cleanly.
        The hard path is for a session that never became ready: setting an event
        cannot interrupt an await that is still blocked, so pending tasks are
        cancelled and the private loop is stopped.
        """
        loop = self._loop
        if loop is not None and not loop.is_closed():
            if hard:
                def _cancel_all() -> None:
                    for task in asyncio.all_tasks(loop):
                        task.cancel()
                    loop.stop()

                try:
                    loop.call_soon_threadsafe(_cancel_all)
                except RuntimeError:
                    LOGGER.debug("loop already closed during hard shutdown", exc_info=True)
            elif self._stop is not None:
                try:
                    loop.call_soon_threadsafe(self._stop.set)
                except RuntimeError:
                    pass
        if self._thread is not None:
            thread = self._thread
            thread.join(timeout=min(self.timeout, 10.0))
            self._last_thread = thread
            self._thread = None

    @property
    def leaked(self) -> bool:
        """True when a helper thread outlived shutdown (a bug, asserted in tests)."""
        current = self._thread or self._last_thread
        return current is not None and current.is_alive()

    def close(self) -> None:
        """Idempotent shutdown; safe to call twice or after an exception."""
        if self._closed:
            return
        self._closed = True
        self._shutdown_thread()

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- calls -----------------------------------------------------------
    def _submit(self, coro):
        if self._closed:
            raise RuntimeError("MCP session is closed")
        if self._loop is None or self._client is None:
            raise RuntimeError("MCP session is not ready")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(self.timeout)
        except TimeoutError:
            future.cancel()
            raise TimeoutError(f"MCP call exceeded {self.timeout}s") from None

    def call(self, name: str, arguments: dict) -> tuple[dict, bool, float]:
        """Return (payload, is_error, latency_seconds)."""
        if self._closed:
            raise RuntimeError("MCP session is closed")
        if self._client is None:
            raise RuntimeError("MCP session is not ready")
        started = time.perf_counter()
        result = self._submit(self._client.call_tool(name, arguments))
        latency = round(time.perf_counter() - started, 4)
        payload, is_error = decode_result(result)
        self.audit.append(ToolRPC(tool=name, arguments=dict(arguments),
                                  latency_s=latency, is_error=is_error,
                                  result_chars=len(json.dumps(payload))))
        return payload, is_error, latency

    # ---- the TrustedCodebase-compatible surface --------------------------
    # The payload is byte-identical to the in-process tool result: latency lives
    # in `self.audit`, never in the data handed to the model.
    def read_code_context(self, file_path: object, start_line: object,
                          end_line: object) -> dict:
        payload, _is_error, _latency = self.call(
            "read_code_context",
            {"file_path": file_path, "start_line": start_line, "end_line": end_line},
        )
        return payload

    def find_symbol_definition(self, symbol: object) -> dict:
        payload, _is_error, _latency = self.call("find_symbol_definition",
                                                 {"symbol": symbol})
        return payload

    def audit_summary(self) -> dict:
        if not self.audit:
            return {"calls": 0}
        latencies = sorted(r.latency_s for r in self.audit)
        return {
            "calls": len(self.audit),
            "errors": sum(1 for r in self.audit if r.is_error),
            "latency_median_s": round(latencies[len(latencies) // 2], 4),
            "latency_max_s": max(latencies),
        }
