"""Provider-neutral LLM interface used by every phase of the agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ..models import CostEntry

Tier = Literal["planner", "fast"]
Thinking = Literal["minimal", "low", "medium", "high"]


class LLMUnavailable(RuntimeError):
    """Every model in the fallback chain failed with a retryable error or timed out."""


@dataclass
class LLMResponse:
    text: str
    parsed: Any
    model: str
    usage: CostEntry


class LLMClient(Protocol):
    provider: str

    def generate(
        self,
        prompt: str,
        *,
        phase: str,
        tier: Tier = "fast",
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        thinking: Thinking | None = None,
    ) -> LLMResponse: ...

    def model_for(self, tier: Tier) -> str: ...
