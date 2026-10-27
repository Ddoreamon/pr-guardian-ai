"""Tests for configuration and the public-mode cost policy."""

import os

from pr_guardian.settings import get_settings, load_dotenv


def _clear(monkeypatch):
    for name in [
        "PR_GUARDIAN_API_KEY",
        "PR_GUARDIAN_BASE_URL",
        "PR_GUARDIAN_API_BASE",
        "PR_GUARDIAN_MODEL",
        "PR_GUARDIAN_PUBLIC_MODE",
        "PR_GUARDIAN_ALLOW_LIVE_LLM",
        "PR_GUARDIAN_ALLOW_TEST_EXECUTION",
        "PR_GUARDIAN_MAX_LIVE_LLM_CALLS",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_no_key_means_not_configured(monkeypatch):
    _clear(monkeypatch)
    cfg = get_settings()
    assert cfg.llm_configured is False
    assert cfg.live_llm_enabled is False
    assert cfg.llm_status() == "not_configured"


def test_key_but_public_mode_blocks_calls(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_API_KEY", "k")
    monkeypatch.setenv("PR_GUARDIAN_PUBLIC_MODE", "1")
    monkeypatch.setenv("PR_GUARDIAN_ALLOW_LIVE_LLM", "0")
    cfg = get_settings()
    assert cfg.llm_configured is True
    assert cfg.live_llm_enabled is False
    assert cfg.llm_status() == "disabled_by_public_policy"


def test_public_mode_with_explicit_opt_in_allows_calls(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_API_KEY", "k")
    monkeypatch.setenv("PR_GUARDIAN_PUBLIC_MODE", "1")
    monkeypatch.setenv("PR_GUARDIAN_ALLOW_LIVE_LLM", "1")
    cfg = get_settings()
    assert cfg.live_llm_enabled is True
    assert cfg.llm_status() == "enabled"


def test_local_mode_allows_calls_with_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_API_KEY", "k")
    assert get_settings().live_llm_enabled is True


def test_base_url_variable_and_alias(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_BASE_URL", "https://primary.example/v1")
    assert get_settings().base_url == "https://primary.example/v1"

    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_API_BASE", "https://alias.example/v1")
    assert get_settings().base_url == "https://alias.example/v1"


def test_defaults(monkeypatch):
    _clear(monkeypatch)
    cfg = get_settings()
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.allow_test_execution is True
    assert cfg.max_live_llm_calls == 50


def test_test_execution_can_be_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PR_GUARDIAN_ALLOW_TEST_EXECUTION", "0")
    assert get_settings().allow_test_execution is False


def test_dotenv_sets_missing_values_only(tmp_path, monkeypatch):
    _clear(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PR_GUARDIAN_MODEL=from-file\nPR_GUARDIAN_BASE_URL=https://file.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PR_GUARDIAN_MODEL", "from-env")
    load_dotenv(env_file)
    assert os.environ["PR_GUARDIAN_MODEL"] == "from-env"          # real env wins
    assert os.environ["PR_GUARDIAN_BASE_URL"] == "https://file.example/v1"
