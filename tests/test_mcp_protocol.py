"""Real MCP protocol tests: a stdio client talking to the stdio server.

No in-process shortcuts and no mocks: each test starts the server as a child
process, performs the MCP handshake, then calls tools over the wire.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pr_guardian.agent_tools import TrustedCodebase

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


APP_MAIN = '''"""Application entry."""

LIMIT_MAIN_MARKER = 100


def apply(total):
    """Apply a discount when the total reaches the limit."""
    if total >= LIMIT_MAIN_MARKER:
        return total * 0.1
    return 0.0
'''

APP_FEATURE = '''"""Application entry."""

LIMIT_FEATURE_MARKER = 200


def apply(total):
    """Apply a discount when the total reaches the limit."""
    if total >= LIMIT_FEATURE_MARKER:
        return total * 0.1
    return 0.0
'''

CONFIG = '''"""Constants used by the app."""

DISCOUNT_RATE = 0.10
TAX_RATE: float = 0.20
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "m@example.com")
    _git(root, "config", "user.name", "m")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app.py").write_text(APP_MAIN, encoding="utf-8")
    (root / "src" / "config.py").write_text(CONFIG, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "docs" / "notes.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / ".env").write_text("PR_GUARDIAN_API_KEY=sk-never-read\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    (root / "src" / "app.py").write_text(APP_FEATURE, encoding="utf-8")
    _git(root, "commit", "-qam", "feature")
    _git(root, "checkout", "-q", "main")
    return root


def _sha(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).strip()


# ---- protocol --------------------------------------------------------------

async def test_list_tools_returns_the_real_definitions(repo):
    from pr_guardian.mcp_client import open_client

    async with open_client(repo, "feature") as client:
        result = await client.list_tools()
        names = [t.name for t in result.tools]
        assert names == ["read_code_context", "find_symbol_definition", "search_code"]
        by_name = {t.name: t for t in result.tools}
        schema = by_name["read_code_context"].input_schema
        assert schema["required"] == ["file_path", "start_line", "end_line"]
        assert "symbol" in by_name["find_symbol_definition"].input_schema["required"]
        assert all(t.description for t in result.tools)


async def test_call_tool_reads_the_pinned_commit(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(client, "read_code_context", {
            "file_path": "src/app.py", "start_line": 1, "end_line": 5,
        })
    assert is_error is False
    assert payload["ok"] is True
    assert "LIMIT_FEATURE_MARKER" in payload["content"]
    assert "LIMIT_MAIN_MARKER" not in payload["content"]
    assert payload["ref"] == _sha(repo, "feature")


async def test_worktree_branch_and_edits_do_not_change_the_snapshot(repo):
    """The server pins the commit at startup, not the working tree."""
    from pr_guardian.mcp_client import call_tool_json, open_client

    _git(repo, "checkout", "-q", "feature")
    (repo / "src" / "app.py").write_text("# WORKTREE_EDIT\nLIMIT = 1\n", encoding="utf-8")
    async with open_client(repo, "main") as client:
        payload, _ = await call_tool_json(client, "read_code_context", {
            "file_path": "src/app.py", "start_line": 1, "end_line": 5,
        })
    assert "LIMIT_MAIN_MARKER" in payload["content"]
    assert "WORKTREE_EDIT" not in payload["content"]
    assert payload["ref"] == _sha(repo, "main")


async def test_call_tool_finds_functions_and_constants(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        for symbol, kind in (("apply", "function"),
                             ("DISCOUNT_RATE", "constant"),
                             ("TAX_RATE", "annotated_constant")):
            payload, is_error = await call_tool_json(
                client, "find_symbol_definition", {"symbol": symbol})
            assert is_error is False, symbol
            assert payload["found"] is True, symbol
            assert payload["matches"][0]["kind"] == kind


# ---- refusals and errors ---------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("tests/test_app.py", "denied_path"),
    ("docs/notes.py", "denied_path"),
    (".env", "denied_path"),
    ("src/../tests/test_app.py", "denied_path"),
    ("/etc/passwd", "denied_path"),
    ("src/notes.txt", "unsupported_type"),
    ("src/missing.py", "not_found"),
])
async def test_unsafe_paths_are_refused_over_the_protocol(repo, path, expected):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(client, "read_code_context", {
            "file_path": path, "start_line": 1, "end_line": 3,
        })
    assert is_error is True
    assert payload["error"]["code"] == expected
    assert "sk-never-read" not in json.dumps(payload)


async def test_bad_arguments_are_reported_not_crashed(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(
            client, "read_code_context", {"file_path": "src/app.py"})
        assert is_error is True
        assert payload["error"]["code"] == "missing_parameter"
        assert "start_line" in payload["error"]["message"]

        payload, is_error = await call_tool_json(
            client, "read_code_context",
            {"file_path": "src/app.py", "start_line": 1, "end_line": 999})
        assert is_error is True
        assert payload["error"]["code"] == "bad_range"

        payload, is_error = await call_tool_json(
            client, "find_symbol_definition", {"symbol": "not a symbol"})
        assert is_error is True
        assert payload["error"]["code"] == "bad_symbol"


async def test_search_code_returns_ranked_chunks_from_the_pinned_commit(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(client, "search_code", {
            "query": "apply discount limit", "top_k": 3,
        })
    assert is_error is False
    assert payload["found"] is True
    assert payload["ref"] == _sha(repo, "feature")
    top = payload["hits"][0]
    assert top["file_path"] == "src/app.py"
    assert top["ref"] == payload["ref"]
    assert top["score"] > 0
    assert top["why"]
    assert "snippet" in top


async def test_search_code_miss_is_explicit(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(
            client, "search_code", {"query": "quantum entanglement"})
    assert is_error is False
    assert payload["found"] is False
    assert payload["hits"] == []
    assert payload["count"] == 0
    assert payload["reason"]


@pytest.mark.parametrize("tool,arguments,expected", [
    ("read_code_context", {"file_path": "src/app.py", "start_line": 1,
                           "end_line": 2, "extra": 1}, "unknown_parameter"),
    ("find_symbol_definition", {"symbol": "apply", "extra": 1}, "unknown_parameter"),
    ("read_code_context", {"file_path": "src/app.py", "start_line": "1",
                           "end_line": 2}, "bad_type"),
    ("read_code_context", {"file_path": "src/app.py", "start_line": True,
                           "end_line": 2}, "bad_type"),
    ("read_code_context", {"file_path": 7, "start_line": 1, "end_line": 2}, "bad_type"),
    ("find_symbol_definition", {"symbol": 3}, "bad_type"),
    ("find_symbol_definition", {}, "missing_parameter"),
    ("search_code", {}, "missing_parameter"),
    ("search_code", {"query": 5}, "bad_type"),
    ("search_code", {"query": "x", "top_k": "3"}, "bad_type"),
    ("search_code", {"query": "x", "repo": "/etc"}, "unknown_parameter"),
])
async def test_schema_violations_are_structured_errors(repo, tool, arguments, expected):
    """The low-level Server does not enforce the schema, so the server must."""
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(client, tool, arguments)
    assert is_error is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected


async def test_unknown_symbol_is_a_clear_miss(repo):
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(
            client, "find_symbol_definition", {"symbol": "no_such_symbol"})
    assert is_error is False
    assert payload["ok"] is True
    assert payload["found"] is False
    assert payload["matches"] == []


async def test_server_cannot_be_talked_into_running_commands(repo, tmp_path):
    from pr_guardian.mcp_client import call_tool_json, open_client

    marker = tmp_path / "should_not_exist.txt"
    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(
            client, "run_shell",
            {"command": f"echo pwned > {marker}"})
    assert is_error is True
    assert payload["error"]["code"] == "unknown_tool"
    assert not marker.exists()


async def test_no_tool_can_switch_repository_or_commit(repo):
    """There is no parameter for repo/ref: extra keys are rejected outright."""
    from pr_guardian.mcp_client import call_tool_json, open_client

    async with open_client(repo, "feature") as client:
        payload, is_error = await call_tool_json(client, "read_code_context", {
            "file_path": "src/app.py", "start_line": 1, "end_line": 2,
            "repo": "/etc", "ref": "main", "sha": "0" * 40,
        })
        assert is_error is True
        assert payload["error"]["code"] == "unknown_parameter"
        assert "ref" in payload["error"]["message"]
        assert payload["error"]["details"]["declared"] == [
            "end_line", "file_path", "start_line"]

        # and the declared call still serves the pinned commit
        ok, is_error = await call_tool_json(client, "read_code_context", {
            "file_path": "src/app.py", "start_line": 1, "end_line": 2})
        assert is_error is False
        assert ok["ref"] == _sha(repo, "feature")


# ---- process hygiene -------------------------------------------------------

def test_stdout_stays_clean_when_startup_fails(tmp_path):
    """A failed start must not write to stdout (that is the protocol channel)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pr_guardian.mcp_server",
         "--repo", str(tmp_path), "--ref", "definitely-not-a-ref"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "cannot bind snapshot" in proc.stderr


def test_server_reports_the_pinned_sha_on_stderr(tmp_path):
    """`git` failures are fatal and reported, again without touching stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "pr_guardian.mcp_server", "--repo", ".", "--ref", "main"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(tmp_path),
    )
    assert proc.returncode != 0
    assert proc.stdout == ""


# ---- parity with the in-process tools --------------------------------------

async def test_mcp_backend_matches_direct_tools(repo):
    """Same snapshot, same answers: MCP is transport, not different logic."""
    from pr_guardian.mcp_client import MCPToolBackend

    direct = TrustedCodebase.from_git(repo, "feature")
    with MCPToolBackend(repo, "feature") as backend:
        read_direct = direct.read_code_context("src/app.py", 1, 6)
        read_mcp = backend.read_code_context("src/app.py", 1, 6)
        symbol_direct = direct.find_symbol_definition("TAX_RATE")
        symbol_mcp = backend.find_symbol_definition("TAX_RATE")
        symbol_fn_direct = direct.find_symbol_definition("apply")
        symbol_fn_mcp = backend.find_symbol_definition("apply")

    read_mcp.pop("mcp_latency_s", None)
    symbol_mcp.pop("mcp_latency_s", None)
    symbol_fn_mcp.pop("mcp_latency_s", None)
    assert read_mcp == read_direct
    assert symbol_mcp == symbol_direct
    assert symbol_fn_mcp == symbol_fn_direct
