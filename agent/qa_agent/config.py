"""Runtime settings loaded from agent/.env (gitignored) and the process environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

AGENT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = AGENT_ROOT / ".env"

Provider = Literal["gemini", "claude"]


class Settings(BaseModel):
    provider: Provider = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_model_planner: str = "gemini-3.8-flash"
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    # Tried in order when a model is overloaded (503) or rate limited (429).
    gemini_model_fallbacks: list[str] = Field(default_factory=lambda: ["gemini-3.7-flash", "gemini-3.5-flash"])
    anthropic_api_key: SecretStr | None = None
    target_url: str = "http://localhost:5173"
    runs_dir: Path = Field(default=AGENT_ROOT / "runs")

    def api_key_for(self, provider: Provider) -> SecretStr | None:
        return self.gemini_api_key if provider == "gemini" else self.anthropic_api_key


def _secret(name: str) -> SecretStr | None:
    value = os.getenv(name, "").strip().strip('"').strip("'")
    return SecretStr(value) if value else None


def _text(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def load_settings(env_file: Path | None = None) -> Settings:
    """Process environment wins over the .env file so CI and one-off overrides work."""
    load_dotenv(env_file or DEFAULT_ENV_FILE, override=False)
    defaults = Settings()
    provider = _text("QA_LLM_PROVIDER", defaults.provider)
    if provider not in ("gemini", "claude"):
        raise ValueError(f"QA_LLM_PROVIDER must be 'gemini' or 'claude', got {provider!r}")
    return Settings(
        provider=provider,  # type: ignore[arg-type]
        gemini_api_key=_secret("GEMINI_API_KEY"),
        gemini_model_planner=_text("GEMINI_MODEL_PLANNER", defaults.gemini_model_planner),
        gemini_model_fast=_text("GEMINI_MODEL_FAST", defaults.gemini_model_fast),
        gemini_model_fallbacks=[
            m.strip() for m in _text("GEMINI_MODEL_FALLBACKS", ",".join(defaults.gemini_model_fallbacks)).split(",")
            if m.strip()
        ],
        anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
        target_url=_text("QA_TARGET_URL", defaults.target_url).rstrip("/"),
    )
