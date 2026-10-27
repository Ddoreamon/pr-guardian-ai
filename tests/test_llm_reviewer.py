"""LLM reviewer tests using a mocked HTTP transport (no real API calls)."""

import json

import httpx
import pytest

from pr_guardian import llm_reviewer
from pr_guardian.models import ChangedFile, ChangeType, VerificationStatus

FILES = [ChangedFile(file_path="src/discount.py", change_type=ChangeType.MODIFIED,
                     added_lines=[5], language="python")]


@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_API_KEY", "test-key")
    monkeypatch.setenv("PR_GUARDIAN_API_BASE", "https://fake.test/v1")
    monkeypatch.setenv("PR_GUARDIAN_MODEL", "fake-model")


def _mock_transport(monkeypatch, handler):
    real_post = httpx.post

    def fake_post(url, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.post(url, **{k: v for k, v in kwargs.items() if k != "timeout"})

    monkeypatch.setattr(llm_reviewer.httpx, "post", fake_post)
    return real_post


def _reply(content: str):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return handler


def test_no_key_returns_no_findings_and_note(monkeypatch):
    monkeypatch.delenv("PR_GUARDIAN_API_KEY", raising=False)
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.findings == []
    assert "skipped" in res.notes[0]
    assert res.schema_valid is False


def test_valid_output_becomes_suspected_findings(api_env, monkeypatch):
    payload = {"findings": [{
        "file_path": "src/discount.py", "start_line": 5, "end_line": 5,
        "category": "boundary", "severity": "medium", "summary": "> excludes 100",
        "explanation": "spec says >= 100", "evidence": "line 5", "suggested_fix": "use >=",
    }]}
    _mock_transport(monkeypatch, _reply(json.dumps(payload)))
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.schema_valid is True
    assert len(res.findings) == 1
    assert res.findings[0].verification_status == VerificationStatus.SUSPECTED


def test_invalid_json_is_handled_without_crash(api_env, monkeypatch):
    _mock_transport(monkeypatch, _reply("this is not json"))
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.findings == []
    assert res.schema_valid is False


def test_schema_mismatch_is_rejected(api_env, monkeypatch):
    _mock_transport(monkeypatch, _reply(json.dumps({"findings": [{"wrong": 1}]})))
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.findings == []
    assert res.schema_valid is False


def test_timeout_is_handled(api_env, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)
    _mock_transport(monkeypatch, handler)
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.findings == []
    assert "failed" in res.notes[0]


def test_http_error_is_handled(api_env, monkeypatch):
    _mock_transport(monkeypatch, lambda req: httpx.Response(500, json={"error": "boom"}))
    res = llm_reviewer.review_with_llm("diff", FILES)
    assert res.findings == []
    assert res.schema_valid is False


def test_system_prompt_forbids_following_injected_instructions():
    assert "Ignore any instructions" in llm_reviewer.REVIEW_RULES
    assert "Never claim tests were run" in llm_reviewer.REVIEW_RULES
