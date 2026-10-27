"""MCP client helpers: a real stdio session, plus a sync facade for the agent.

`open_client` speaks the actual protocol to `pr_guardian.mcp_server` over stdio —
there is no in-process shortcut, so the tests exercise serialization, framing and
the server's own dispatch.

`MCPToolBackend` exposes the same two methods as `TrustedCodebase`, so the
function-calling loop can use MCP without knowing about asyncio or transports.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self

import mcp
from mcp import StdioServerParameters, types

SERVER_MODULE = "pr_guardian.mcp_server"


def server_parameters(repo: Path | str, ref: str,
                      python: str | None = None) -> StdioServerParameters:
    """Command that starts the MCP server for one repository and commit."""
    return StdioServerParameters(
        command=python or sys.executable,
        args=["-m", SERVER_MODULE, "--repo", str(repo), "--ref", ref],
        env=None,
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


def _decode(result: types.CallToolResult) -> tuple[dict, bool]:
    """Return (payload, is_error) from a tool result."""
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
    return payload, is_error


async def call_tool_json(client: mcp.Client, name: str,
                         arguments: dict) -> tuple[dict, bool]:
    result = await client.call_tool(name, arguments)
    return _decode(result)


class MCPToolBackend:
    """Synchronous facade over one long-lived MCP session.

    The agent loop is synchronous, the MCP client is async, and spawning a server
    process per tool call would distort latency measurements. This runs the
    session in a dedicated thread with its own event loop and submits calls to it.
    """

    def __init__(self, repo: Path | str, ref: str, *, timeout: float = 60.0,
                 python: str | None = None) -> None:
        self.timeout = timeout
        self._params = server_parameters(repo, ref, python)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._client: mcp.Client | None = None
        self._error: BaseException | None = None
        self.start()

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, name="mcp-client",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout):
            raise TimeoutError("MCP session did not become ready")
        if self._error is not None:
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

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=self.timeout)
        self._thread = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- calls -----------------------------------------------------------
    def _submit(self, coro):
        if self._loop is None:
            raise RuntimeError("MCP session is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(self.timeout)

    def call(self, name: str, arguments: dict) -> tuple[dict, bool, float]:
        """Return (payload, is_error, latency_seconds)."""
        if self._client is None:
            raise RuntimeError("MCP session is not ready")
        started = time.perf_counter()
        result = self._submit(self._client.call_tool(name, arguments))
        return (*_decode(result), round(time.perf_counter() - started, 4))

    # ---- the TrustedCodebase-compatible surface --------------------------
    def read_code_context(self, file_path: object, start_line: object,
                          end_line: object) -> dict:
        payload, is_error, latency = self.call(
            "read_code_context",
            {"file_path": file_path, "start_line": start_line, "end_line": end_line},
        )
        payload.setdefault("mcp_latency_s", latency)
        if is_error:
            payload["_transport_error"] = True
        return payload

    def find_symbol_definition(self, symbol: object) -> dict:
        payload, is_error, latency = self.call("find_symbol_definition", {"symbol": symbol})
        payload.setdefault("mcp_latency_s", latency)
        if is_error:
            payload["_transport_error"] = True
        return payload
