"""MCP session lifecycle: failures must not leak threads or server processes."""

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters, types

from pr_guardian import mcp_client
from pr_guardian.mcp_client import MCPToolBackend, decode_result


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "lifecycle-repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "l@example.com")
    _git(root, "config", "user.name", "l")
    (root / "src" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _params_running(code: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-c", code],
                                 encoding="utf-8", encoding_error_handler="replace")


def _wait_dead(thread: threading.Thread, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not thread.is_alive():
            return True
        time.sleep(0.05)
    return not thread.is_alive()


# ---- startup failures ------------------------------------------------------

def test_start_timeout_raises_and_reaps_the_thread(repo, monkeypatch):
    monkeypatch.setattr(mcp_client, "server_parameters",
                        lambda *a, **k: _params_running("import time; time.sleep(30)"))
    backend = MCPToolBackend(repo, "main", timeout=0.5, autostart=False)
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        backend.start()
    elapsed = time.perf_counter() - started

    assert elapsed < 10, f"start() blocked for {elapsed:.1f}s"
    assert backend._last_thread is not None
    assert _wait_dead(backend._last_thread), "helper thread outlived the timeout"
    assert backend.leaked is False
    backend.close()  # safe after a failed start
    assert backend.is_closed


def test_server_exiting_early_raises_runtime_error(repo, monkeypatch):
    monkeypatch.setattr(mcp_client, "server_parameters",
                        lambda *a, **k: _params_running("raise SystemExit(3)"))
    backend = MCPToolBackend(repo, "main", timeout=10, autostart=False)
    with pytest.raises(RuntimeError, match="MCP session failed"):
        backend.start()
    assert backend._last_thread is not None
    assert _wait_dead(backend._last_thread)
    backend.close()


# ---- RPC failures ----------------------------------------------------------

def test_rpc_timeout_raises_and_still_closes(repo):
    backend = MCPToolBackend(repo, "main", timeout=5)
    try:
        class SlowClient:
            async def call_tool(self, name, arguments=None, **_kw):
                await asyncio.sleep(0.6)
                return types.CallToolResult(content=[], is_error=False)

        backend._client = SlowClient()
        backend.timeout = 0.1
        with pytest.raises(TimeoutError, match="exceeded"):
            backend.call("read_code_context", {"file_path": "src/mod.py",
                                               "start_line": 1, "end_line": 1})
    finally:
        backend.close()
    assert backend.is_closed


def test_close_is_idempotent_and_safe_after_errors(repo):
    backend = MCPToolBackend(repo, "main")
    thread = backend._thread
    assert thread is not None and thread.is_alive()
    backend.close()
    backend.close()
    backend.close()
    assert backend.is_closed
    assert _wait_dead(thread), "session thread survived close()"
    with pytest.raises(RuntimeError, match="closed"):
        backend.call("read_code_context", {"file_path": "src/mod.py",
                                           "start_line": 1, "end_line": 1})


def test_a_failed_call_does_not_break_the_session(repo):
    backend = MCPToolBackend(repo, "main")
    try:
        # an unsafe path is an error result, not a transport failure
        payload = backend.read_code_context("tests/x.py", 1, 2)
        assert payload["ok"] is False
        # the session still works afterwards
        ok = backend.read_code_context("src/mod.py", 1, 1)
        assert ok["ok"] is True
    finally:
        backend.close()


# ---- leaks -----------------------------------------------------------------

def test_no_helper_thread_is_left_running(repo):
    before = {t.ident for t in threading.enumerate()}
    with MCPToolBackend(repo, "main") as backend:
        assert backend.read_code_context("src/mod.py", 1, 1)["ok"] is True
    deadline = time.time() + 10
    while time.time() < deadline:
        leaked = [t for t in threading.enumerate()
                  if t.ident is not None and t.ident not in before
                  and t.name.startswith("mcp-client")]
        if not leaked:
            break
        time.sleep(0.05)
    assert not leaked, f"leftover threads: {leaked}"


def test_no_server_process_is_left_running(repo):
    psutil = pytest.importorskip("psutil")
    marker = str(repo)

    def server_children():
        out = []
        for child in psutil.Process().children(recursive=True):
            try:
                if marker in " ".join(child.cmdline()):
                    out.append(child)
            except psutil.Error:
                continue
        return out

    with MCPToolBackend(repo, "main") as backend:
        assert backend.read_code_context("src/mod.py", 1, 1)["ok"] is True
        during = server_children()

    deadline = time.time() + 15
    while time.time() < deadline and server_children():
        time.sleep(0.1)
    assert server_children() == [], "server process outlived the session"
    assert during, "expected a server child process while the session was open"


# ---- protocol error semantics ---------------------------------------------

def test_is_error_wins_even_when_the_payload_claims_success():
    """A tool that returns is_error=True with {"ok": true} is still an error."""
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text='{"ok": true, "ref": "deadbeef"}')],
        is_error=True,
    )
    payload, is_error = decode_result(result)
    assert is_error is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == "transport_error"


def test_is_error_keeps_a_structured_error():
    result = types.CallToolResult(
        content=[types.TextContent(
            type="text",
            text='{"ok": true, "error": {"code": "denied_path", "message": "nope"}}')],
        is_error=True,
    )
    payload, is_error = decode_result(result)
    assert is_error is True
    assert payload == {"ok": False, "error": {"code": "denied_path", "message": "nope"}}


def test_invalid_json_content_becomes_an_error_payload():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="<html>not json</html>")],
        is_error=False,
    )
    payload, is_error = decode_result(result)
    assert is_error is False
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_payload"
