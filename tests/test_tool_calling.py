"""Protocol tests for the tool-calling loop, using a mocked HTTP transport.

No network and no model: these assert the wire protocol, the budget, error
handling and the refusal paths. Real-model behaviour is verified separately.
"""

import json

import httpx
import pytest

from pr_guardian import tool_calling
from pr_guardian.models import ChangedFile, ChangeType, VerificationStatus
from pr_guardian.settings import Settings

FINDINGS_JSON = json.dumps({"findings": [{
    "file_path": "src/a.py", "start_line": 2, "end_line": 2,
    "category": "boundary", "severity": "medium",
    "summary": "off-by-one", "explanation": "e", "evidence": "ev", "suggested_fix": "f",
}]})


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "fake-model",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": True,
        "max_live_llm_calls": 50, "tool_calls_enabled": True,
        "max_tool_rounds": 3, "max_tool_calls": 4,
    }
    base.update(over)
    return Settings(**base)


def _msg(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _tool_call(call_id: str, name: str, args) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": args if isinstance(args, str) else json.dumps(args),
        },
    }


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def codebase(tmp_path):
    from pr_guardian.agent_tools import TrustedCodebase

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / ".env").write_text("PR_GUARDIAN_API_KEY=sk-leak-me\n", encoding="utf-8")
    return TrustedCodebase(root)


class _Queue:
    """Callable response queue that also records what was sent."""

    def __init__(self):
        self.queue = []
        self.sent = []

    def __call__(self, items):
        self.queue.extend(items)

    def take(self):
        if not self.queue:
            raise AssertionError("unexpected extra model call")
        return self.queue.pop(0)


@pytest.fixture
def queued(monkeypatch):
    q = _Queue()

    def fake_post(url, headers=None, json=None, timeout=None):
        q.sent.append({"url": url, "payload": json})
        return _Resp(q.take())

    monkeypatch.setattr(tool_calling.httpx, "post", fake_post)
    return q


FILES = [ChangedFile(file_path="src/a.py", change_type=ChangeType.MODIFIED,
                     added_lines=[2], language="python")]


# ---- happy path ------------------------------------------------------------

def test_tool_call_then_final_answer(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("call_1", "read_code_context",
                                    {"file_path": "src/a.py", "start_line": 1, "end_line": 2})]),
        _msg(content=FINDINGS_JSON),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())

    assert res.status == "ok"
    assert res.schema_valid is True
    assert len(res.findings) == 1
    assert res.findings[0].verification_status == VerificationStatus.SUSPECTED
    assert len(res.trace) == 1
    assert res.trace[0].ok is True and res.trace[0].name == "read_code_context"
    assert res.usage == {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240}


def test_request_declares_tools_and_replies_with_matching_tool_id(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("call_abc", "read_code_context",
                                    {"file_path": "src/a.py", "start_line": 1, "end_line": 2})]),
        _msg(content=FINDINGS_JSON),
    ])
    tool_calling.review_with_tools("diff", FILES, codebase, _settings())

    first = queued.sent[0]["payload"]
    assert [t["function"]["name"] for t in first["tools"]] == [
        "read_code_context", "find_symbol_definition",
    ]

    second_msgs = queued.sent[1]["payload"]["messages"]
    assistant = next(m for m in second_msgs if m["role"] == "assistant")
    tool_msgs = [m for m in second_msgs if m["role"] == "tool"]
    assert assistant["tool_calls"][0]["id"] == "call_abc"
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_abc"
    # result is fenced and labelled as untrusted data
    assert "UNTRUSTED TOOL OUTPUT" in tool_msgs[0]["content"]
    assert "x = 1" in tool_msgs[0]["content"]
    assert "y = 2" in tool_msgs[0]["content"]


def test_find_symbol_tool_can_be_called(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("c1", "find_symbol_definition", {"symbol": "helper"})]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.trace[0].ok is True
    assert res.trace[0].name == "find_symbol_definition"
    assert res.findings == []


# ---- refusals and error paths ---------------------------------------------

def test_unknown_tool_is_refused_not_executed(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("c1", "run_shell", {"cmd": "rm -rf /"})]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.trace[0].ok is False
    assert res.trace[0].error_code == "unknown_tool"
    assert res.status in {"ok", "tool_limit"}


def test_path_traversal_is_refused_and_secret_never_returned(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("c1", "read_code_context",
                                    {"file_path": ".env", "start_line": 1, "end_line": 5})]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())

    assert res.trace[0].ok is False
    assert res.trace[0].error_code == "denied_path"
    tool_msg = next(m for m in queued.sent[1]["payload"]["messages"] if m["role"] == "tool")
    assert "sk-leak-me" not in tool_msg["content"]


def test_malformed_tool_arguments_are_reported(queued, codebase):
    queued([
        _msg(tool_calls=[_tool_call("c1", "read_code_context", "{not json")]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.trace[0].ok is False
    assert res.trace[0].error_code == "bad_arguments"


def test_model_http_error_is_classified(monkeypatch, codebase):
    def boom(*a, **k):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(tool_calling.httpx, "post", boom)
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.status == "model_error"
    assert res.findings == []
    assert res.error.startswith("http:")


def test_unexpected_response_shape_is_classified(monkeypatch, codebase):
    monkeypatch.setattr(tool_calling.httpx, "post",
                        lambda *a, **k: _Resp({"choices": []}))
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.status == "model_error"
    assert res.error == "bad_response_shape"


def test_no_key_makes_no_request(monkeypatch, codebase):
    def fail(*a, **k):
        raise AssertionError("must not call the provider without a key")

    monkeypatch.setattr(tool_calling.httpx, "post", fail)
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings(api_key=""))
    assert res.status == "not_configured"
    assert res.findings == []


def test_public_policy_blocks_calls(monkeypatch, codebase):
    def fail(*a, **k):
        raise AssertionError("must not call the provider when policy blocks it")

    monkeypatch.setattr(tool_calling.httpx, "post", fail)
    cfg = _settings(public_mode=True, allow_live_llm=False)
    res = tool_calling.review_with_tools("diff", FILES, codebase, cfg)
    assert res.status == "disabled"
    assert res.findings == []


# ---- budget ----------------------------------------------------------------

def test_tool_call_budget_is_enforced(queued, codebase):
    call = _tool_call("c", "read_code_context", {"file_path": "src/a.py", "start_line": 1, "end_line": 1})
    queued([
        _msg(tool_calls=[call]), _msg(tool_calls=[call]), _msg(tool_calls=[call]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings(max_tool_calls=2))

    executed = [t for t in res.trace if t.ok]
    refused = [t for t in res.trace if not t.ok]
    assert len(executed) == 2
    assert refused and refused[0].error_code == "budget_exceeded"
    assert res.status == "tool_limit"
    # every requested call still got a tool reply, so the transcript stays valid
    tool_msgs = [m for m in queued.sent[-1]["payload"]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == len(res.trace)


def test_round_limit_is_enforced(queued, codebase):
    call = _tool_call("c", "find_symbol_definition", {"symbol": "helper"})
    queued([
        _msg(tool_calls=[call]), _msg(tool_calls=[call]), _msg(tool_calls=[call]),
        _msg(content='{"findings": []}'),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase,
                                         _settings(max_tool_rounds=2, max_tool_calls=8))
    assert res.status == "tool_limit"
    # 2 tool rounds + 1 final JSON call
    assert len(queued.sent) == 3


# ---- final JSON handling ---------------------------------------------------

def test_invalid_final_content_triggers_json_repair_call(queued, codebase):
    queued([
        _msg(content="I think there is a bug here."),
        _msg(content=FINDINGS_JSON),
    ])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.schema_valid is True
    assert len(res.findings) == 1
    repair = queued.sent[1]["payload"]
    assert repair["response_format"] == {"type": "json_object"}
    assert "tools" not in repair


def test_unrepaired_output_is_schema_invalid(queued, codebase):
    queued([_msg(content="still prose"), _msg(content="also prose")])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.schema_valid is False
    assert res.status == "schema_invalid"
    assert res.findings == []


def test_findings_are_always_suspected(queued, codebase):
    confirmed = json.dumps({"findings": [{
        "file_path": "src/a.py", "start_line": 2, "end_line": 2,
        "category": "logic", "severity": "high", "summary": "s", "status": "confirmed",
    }]})
    queued([_msg(content=confirmed)])
    res = tool_calling.review_with_tools("diff", FILES, codebase, _settings())
    assert res.findings[0].verification_status == VerificationStatus.SUSPECTED
