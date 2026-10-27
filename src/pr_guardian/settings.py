"""Runtime configuration from environment variables and an optional .env file.

Supported variables:
  PR_GUARDIAN_API_KEY              API key for the OpenAI-compatible endpoint
  PR_GUARDIAN_BASE_URL             base URL, e.g. https://api.openai.com/v1
                                   (PR_GUARDIAN_API_BASE is accepted as an alias)
  PR_GUARDIAN_MODEL                model name, e.g. gpt-4o-mini
  PR_GUARDIAN_PUBLIC_MODE          1 = deployed publicly: disable live LLM calls
                                   unless explicitly allowed
  PR_GUARDIAN_ALLOW_LIVE_LLM       1 = allow real model calls in public mode
  PR_GUARDIAN_ALLOW_TEST_EXECUTION 1 = allow pytest on the trusted demo repo
  PR_GUARDIAN_MAX_LIVE_LLM_CALLS   global cap for live model calls (default 50)
  PR_GUARDIAN_TOOL_CALLS_ENABLED   1 = let the model call read-only tools (V2, default off)
  PR_GUARDIAN_MAX_TOOL_ROUNDS      max model turns in the tool loop (default 3, max 5)
  PR_GUARDIAN_MAX_TOOL_CALLS       max tool executions per review (default 4, max 8)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from .env without overriding real environment values."""
    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    public_mode: bool
    allow_live_llm: bool
    allow_test_execution: bool
    max_live_llm_calls: int
    tool_calls_enabled: bool = False
    max_tool_rounds: int = 3
    max_tool_calls: int = 4

    @property
    def llm_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def live_llm_enabled(self) -> bool:
        """True only when a key exists AND policy allows calling the provider."""
        if not self.api_key:
            return False
        return (not self.public_mode) or self.allow_live_llm

    def llm_status(self) -> str:
        """Human-readable reason the LLM step will or will not run."""
        if not self.api_key:
            return "not_configured"
        if not self.live_llm_enabled:
            return "disabled_by_public_policy"
        return "enabled"


def get_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("PR_GUARDIAN_API_KEY", "").strip(),
        base_url=(
            os.environ.get("PR_GUARDIAN_BASE_URL")
            or os.environ.get("PR_GUARDIAN_API_BASE")
            or "https://api.openai.com/v1"
        ).strip(),
        model=os.environ.get("PR_GUARDIAN_MODEL", "gpt-4o-mini").strip(),
        public_mode=_flag("PR_GUARDIAN_PUBLIC_MODE"),
        allow_live_llm=_flag("PR_GUARDIAN_ALLOW_LIVE_LLM"),
        allow_test_execution=_flag("PR_GUARDIAN_ALLOW_TEST_EXECUTION", default=True),
        max_live_llm_calls=int(os.environ.get("PR_GUARDIAN_MAX_LIVE_LLM_CALLS", "50") or 50),
        # V2: read-only tool calling is opt-in so V1 behaviour is unchanged by default.
        tool_calls_enabled=_flag("PR_GUARDIAN_TOOL_CALLS_ENABLED", default=False),
        max_tool_rounds=max(1, min(int(os.environ.get("PR_GUARDIAN_MAX_TOOL_ROUNDS", "3") or 3), 5)),
        max_tool_calls=max(1, min(int(os.environ.get("PR_GUARDIAN_MAX_TOOL_CALLS", "4") or 4), 8)),
    )
