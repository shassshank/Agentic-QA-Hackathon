"""LLM provider factory."""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMResponse, LLMUnavailable, Tier
from .meter import CostMeter

__all__ = ["CostMeter", "LLMClient", "LLMResponse", "LLMUnavailable", "Tier", "get_client"]


def get_client(settings: Settings, meter: CostMeter) -> LLMClient:
    key = settings.api_key_for(settings.provider)
    if key is None:
        raise RuntimeError(f"No API key configured for provider {settings.provider!r}; set it in agent/.env")
    if settings.provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(
            key.get_secret_value(),
            planner_model=settings.gemini_model_planner,
            fast_model=settings.gemini_model_fast,
            fallbacks=settings.gemini_model_fallbacks,
            meter=meter,
        )
    raise NotImplementedError("The Claude provider lands with the cost-comparison phase (P9)")
