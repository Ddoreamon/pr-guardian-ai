"""MCP integration: backend selection in the pipeline, and tool-level parity."""

import subprocess
from pathlib import Path

import pytest

from pr_guardian import pipeline
from pr_guardian.agent_tools import (
    FIND_SYMBOL_DEFINITION,
    READ_CODE_CONTEXT,
    TrustedCodebase,
)
from pr_guardian.mcp_client import MCPToolBackend
from pr_guardian.settings import Settings
from pr_guardian.tool_calling import _execute


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "fake-model",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": False,
        "max_live_llm_calls": 50, "tool_calls_enabled": True,
        "max_tool_rounds": 3, "max_tool_calls": 4, "tool_backend": "local",
    }
    base.update(over)
    return Settings(**base)


# ---- pipeline backend selection -------------------------------------------

def test_pipeline_uses_the_local_backend_by_default(demo_repo, monkeypatch):
    captured = {}

    def fake(diff_text, files, codebase, settings, max_rounds=None, max_tool_calls=None):
        from pr_guardian.tool_calling import ToolReviewResult

        captured["backend"] = type(codebase).__name__
        return ToolReviewResult([], ["LLM model: fake"], True, model="fake", status="ok")

    monkeypatch.setattr(pipeline, "review_with_tools", fake)
    pipeline.run_review(demo_repo, "main", "feature/clean", use_llm=True, run_tests=False,
                        settings=_settings(tool_backend="local"))
    assert captured["backend"] == "TrustedCodebase"


def test_pipeline_uses_the_mcp_backend_when_configured(demo_repo, monkeypatch):
    captured = {}

    def fake(diff_text, files, codebase, settings, max_rounds=None, max_tool_calls=None):
        from pr_guardian.tool_calling import ToolReviewResult

        captured["backend"] = type(codebase).__name__
        captured["snapshot"] = getattr(getattr(codebase, "_params", None), "args", None)
        return ToolReviewResult([], ["LLM model: fake"], True, model="fake", status="ok")

    monkeypatch.setattr(pipeline, "review_with_tools", fake)
    pipeline.run_review(demo_repo, "main", "feature/clean", use_llm=True, run_tests=False,
                        settings=_settings(tool_backend="mcp"))
    assert captured["backend"] == "MCPToolBackend"
    # the server was started for the reviewed ref, not the working tree
    assert "feature/clean" in (captured["snapshot"] or [])


# ---- tool-level parity between local and MCP ------------------------------

@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "i@example.com")
    _git(root, "config", "user.name", "i")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "mod.py").write_text(
        '"""Module."""\n\nRATE = 0.2\nTAX: float = 0.1\n\n\ndef calc(x):\n    return x * RATE\n',
        encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def test_execute_gives_the_same_result_for_local_and_mcp(repo):
    direct = TrustedCodebase.from_git(repo, "HEAD")
    local_read = _execute(READ_CODE_CONTEXT,
                          {"file_path": "src/mod.py", "start_line": 1, "end_line": 6},
                          direct, 1)
    local_symbol = _execute(FIND_SYMBOL_DEFINITION, {"symbol": "TAX"}, direct, 1)
    local_fn = _execute(FIND_SYMBOL_DEFINITION, {"symbol": "calc"}, direct, 1)

    with MCPToolBackend(repo, "HEAD") as backend:
        mcp_read = _execute(READ_CODE_CONTEXT,
                            {"file_path": "src/mod.py", "start_line": 1, "end_line": 6},
                            backend, 1)
        mcp_symbol = _execute(FIND_SYMBOL_DEFINITION, {"symbol": "TAX"}, backend, 1)
        mcp_fn = _execute(FIND_SYMBOL_DEFINITION, {"symbol": "calc"}, backend, 1)

    for local, mcp in ((local_read, mcp_read), (local_symbol, mcp_symbol), (local_fn, mcp_fn)):
        local_record, local_payload = local
        mcp_record, mcp_payload = mcp
        # the payload handed to the model is identical: no latency key, no metadata
        assert mcp_payload == local_payload
        assert "mcp_latency_s" not in mcp_payload
        assert mcp_record.outcome == local_record.outcome
        assert mcp_record.matched == local_record.matched


def test_backend_keeps_latency_out_of_the_payload_but_in_its_audit(repo):
    with MCPToolBackend(repo, "HEAD") as backend:
        payload = backend.read_code_context("src/mod.py", 1, 4)
        backend.find_symbol_definition("calc")
        summary = backend.audit_summary()
        entries = [r.as_dict() for r in backend.audit]

    assert "mcp_latency_s" not in payload
    assert [e["tool"] for e in entries] == ["read_code_context", "find_symbol_definition"]
    assert all(e["latency_s"] >= 0 for e in entries)
    assert summary["calls"] == 2
    assert summary["errors"] == 0


def test_execute_marks_an_mcp_encoded_error_as_error(repo):
    class Encoded:
        def read_code_context(self, file_path, start_line, end_line):
            return {"ok": False, "error": {"code": "denied_path", "message": "nope"}}

        def find_symbol_definition(self, symbol):
            return {"ok": False, "error": {"code": "denied_path", "message": "nope"}}

    record, payload = _execute(
        READ_CODE_CONTEXT, {"file_path": "tests/x.py", "start_line": 1, "end_line": 2},
        Encoded(), 1)
    assert record.ok is False
    assert record.outcome == "error"
    assert record.error_code == "denied_path"
    assert payload["ok"] is False


def test_execute_survives_a_transport_failure(repo):
    class Broken:
        def read_code_context(self, *a):
            raise RuntimeError("session died")

        def find_symbol_definition(self, *a):
            raise RuntimeError("session died")

    record, payload = _execute(
        READ_CODE_CONTEXT, {"file_path": "src/mod.py", "start_line": 1, "end_line": 2},
        Broken(), 1)
    assert record.ok is False
    assert record.outcome == "error"
    assert record.error_code == "tool_crashed"
    assert payload["error"]["code"] == "tool_crashed"


def test_backend_setting_rejects_unknown_values(monkeypatch):
    from pr_guardian.settings import get_settings

    monkeypatch.setenv("PR_GUARDIAN_TOOL_BACKEND", "carrier-pigeon")
    assert get_settings().tool_backend == "local"
    monkeypatch.setenv("PR_GUARDIAN_TOOL_BACKEND", "mcp")
    assert get_settings().tool_backend == "mcp"

def test_smoke_artefacts_never_contain_host_paths():
    """The smoke report is public: it must not leak the local user or temp path."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "mcp_smoke", Path(__file__).resolve().parents[1] / "scripts" / "mcp_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake = {
        "repo_label": module.DEV_REPO_LABEL,
        "direct_comparison": {
            "pinned_sha": "a" * 40, "init_s": 0.5, "calls": [],
            "all_identical": True, "all_refs_match": True,
            "worktree_edit_ignored": True, "local_median_s": 0.01,
            "mcp_median_s": 0.02, "session_closed": True,
            "leaked_threads": [], "audit_summary": {"calls": 0},
        },
        "cases": [{
            "case": "bug", "backend": "mcp", "branch": "b", "snapshot_sha": "a" * 40,
            "llm_status": "ok", "tool_status": "ok", "tool_model_calls": 1,
            "tool_model_failures": 0, "tool_rounds": 1, "prompt_tokens": 1,
            "completion_tokens": 1, "tool_calls": [], "llm_findings": [],
            "final_findings": [], "leaked_threads": [],
        }],
    }
    text = module.render(fake)
    assert "C:\\\\Users" not in text
    assert "/tmp/" not in text and "/home/" not in text
    assert module.DEV_REPO_LABEL == "<dev-repo>"
