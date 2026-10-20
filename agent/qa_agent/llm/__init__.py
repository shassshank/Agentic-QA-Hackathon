"""LLM provider factory."""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMResponse, LLMUnavailable, Tier
from .meter import CostMeter
from .typesafe import DecisionClient

__all__ = ["CostMeter", "LLMClient", "LLMResponse", "LLMUnavailable", "Tier", "get_client", "get_decider"]


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


def get_decider(settings: Settings, meter: CostMeter) -> DecisionClient | None:
    if settings.typesafe_api_key is None:
        return None
    return DecisionClient(settings.typesafe_api_key.get_secret_value(), model=settings.typesafe_model, meter=meter)
