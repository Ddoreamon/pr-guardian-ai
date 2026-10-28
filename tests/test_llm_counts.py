"""Provider request counting for the non-tool LLM path (no network)."""

import json

import pytest

from pr_guardian import llm_reviewer, pipeline
from pr_guardian.llm_reviewer import LLMResult, review_with_llm
from pr_guardian.settings import Settings


def _settings(**over) -> Settings:
    base = {
        "api_key": "test-key", "base_url": "https://fake.test", "model": "deepseek-flash",
        "public_mode": False, "allow_live_llm": True, "allow_test_execution": False,
        "max_live_llm_calls": 50, "tool_calls_enabled": False,
        "max_tool_rounds": 3, "max_tool_calls": 4,
    }
    base.update(over)
    return Settings(**base)


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> dict:
        return self._payload


FINDINGS = {"findings": [{
    "file_path": "src/a.py", "start_line": 1, "end_line": 2, "category": "logic",
    "severity": "low", "summary": "s", "explanation": "", "evidence": "",
    "suggested_fix": ""}]}


def test_no_key_makes_no_request(monkeypatch):
    called = []
    monkeypatch.setattr(llm_reviewer.httpx, "post", lambda *a, **k: called.append(1))
    result = review_with_llm("diff", [], _settings(api_key=""))
    assert result.attempts == 0
    assert result.failures == 0
    assert called == []


def test_public_mode_makes_no_request(monkeypatch):
    called = []
    monkeypatch.setattr(llm_reviewer.httpx, "post", lambda *a, **k: called.append(1))
    # public mode disables live calls unless allow_live_llm overrides it
    result = review_with_llm("diff", [], _settings(public_mode=True, allow_live_llm=False))
    assert result.attempts == 0
    assert called == []


def test_http_error_is_counted_as_a_failed_request(monkeypatch):
    def boom(*_a, **_k):
        import httpx

        raise httpx.ConnectError("no route")

    monkeypatch.setattr(llm_reviewer.httpx, "post", boom)
    result = review_with_llm("diff", [], _settings())
    assert result.attempts == 1
    assert result.failures == 1
    assert result.error.startswith("http:")


def test_successful_request_is_counted_once(monkeypatch):
    payload = {"choices": [{"message": {"content": json.dumps(FINDINGS)}}],
               "usage": {"prompt_tokens": 42, "completion_tokens": 7}}
    monkeypatch.setattr(llm_reviewer.httpx, "post", lambda *a, **k: FakeResponse(payload))
    result = review_with_llm("diff", [], _settings())
    assert result.attempts == 1
    assert result.failures == 0
    assert result.usage["prompt_tokens"] == 42


def test_schema_invalid_output_still_counted_as_a_request(monkeypatch):
    payload = {"choices": [{"message": {"content": '{"findings": "nope"}'}}], "usage": {}}
    monkeypatch.setattr(llm_reviewer.httpx, "post", lambda *a, **k: FakeResponse(payload))
    result = review_with_llm("diff", [], _settings())
    assert result.attempts == 1
    assert result.failures == 0        # the request succeeded; the content did not
    assert result.schema_valid is False


# ---- pipeline roll-up ------------------------------------------------------

def test_pipeline_records_non_tool_requests(demo_repo, monkeypatch):
    def fake(*_a, **_k):
        return LLMResult([], ["ok"], True, model="deepseek-flash",
                         usage={"prompt_tokens": 100, "completion_tokens": 5},
                         attempts=2, failures=1)

    monkeypatch.setattr(pipeline, "review_with_llm", fake)
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False, settings=_settings())
    assert report.completeness.model_requests == 2
    assert report.completeness.model_failures == 1
    assert report.llm_prompt_tokens == 100
    assert report.model_name == "deepseek-flash"


def test_pipeline_does_not_invent_requests_without_a_model(demo_repo, monkeypatch):
    def fake(*_a, **_k):
        return LLMResult([], ["skipped"], False, model="deepseek-flash",
                         attempts=0, failures=0)

    monkeypatch.setattr(pipeline, "review_with_llm", fake)
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False,
                                 settings=_settings(api_key=""))
    assert report.completeness.model_requests == 0
    assert report.completeness.model_failures == 0
    assert report.review_status == "not_run"
    assert report.model_name == "none"


@pytest.mark.parametrize("status", ["failed", "schema_invalid"])
def test_failed_model_path_reports_failed_status(demo_repo, monkeypatch, status):
    def fake(*_a, **_k):
        return LLMResult([], ["failed"], False, model="deepseek-flash",
                         attempts=1, failures=1, error="http:ConnectError")

    monkeypatch.setattr(pipeline, "review_with_llm", fake)
    report = pipeline.run_review(demo_repo, "main", "feature/boundary-bug",
                                 use_llm=True, run_tests=False, settings=_settings())
    assert report.review_status == "failed"
    assert report.completeness.model_failures == 1
