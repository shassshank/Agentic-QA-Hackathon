"""Gemini provider with request timeouts, retry with backoff and a model fallback chain."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from ..models import CostEntry
from .base import LLMResponse, LLMUnavailable, Thinking, Tier
from .meter import CostMeter

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
THINKING_LEVELS = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}
# Planner calls produce long structured output with deep thinking; fast calls should be snappy.
TIMEOUT_MS = {"planner": 240_000, "fast": 45_000}
MAX_WAIT_SECONDS = 60


def retry_delay_seconds(exc: errors.APIError) -> float | None:
    """Read google.rpc.RetryInfo.retryDelay (e.g. '53540s') from an API error, if present."""
    details = exc.details if isinstance(exc.details, dict) else {}
    for detail in details.get("error", {}).get("details", []) or []:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            raw = str(detail.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


class GeminiClient:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        planner_model: str,
        fast_model: str,
        fallbacks: list[str],
        meter: CostMeter,
        attempts_per_model: int = 2,
        backoff_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clients = {
            tier: genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout))
            for tier, timeout in TIMEOUT_MS.items()
        }
        self._models = {"planner": planner_model, "fast": fast_model}
        self._fallbacks = fallbacks
        self._meter = meter
        self._attempts = attempts_per_model
        self._backoff = backoff_seconds
        self._sleep = sleep

    def model_for(self, tier: Tier) -> str:
        return self._models[tier]

    def _chain(self, tier: Tier) -> list[str]:
        primary = self._models[tier]
        return [primary, *[m for m in self._fallbacks if m != primary]]

    def generate(
        self,
        prompt: str,
        *,
        phase: str,
        tier: Tier = "fast",
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        thinking: Thinking | None = None,
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVELS[thinking]) if thinking else None,
        )
        failures: list[str] = []
        for model in self._chain(tier):
            for attempt in range(self._attempts):
                started = time.monotonic()
                try:
                    response = self._clients[tier].models.generate_content(
                        model=model, contents=prompt, config=config
                    )
                except errors.APIError as exc:
                    if exc.code == 404:
                        # Listed models can still be retired for this key; move on to the next one.
                        failures.append(f"{model}: HTTP 404 (model unavailable)")
                        break
                    if exc.code not in RETRYABLE_STATUS:
                        raise
                    delay = retry_delay_seconds(exc)
                    if delay is not None and delay > MAX_WAIT_SECONDS:
                        # Quota exhausted for a long window (e.g. daily free-tier cap): retrying is futile.
                        failures.append(f"{model}: HTTP {exc.code} (quota, retry in {int(delay)}s)")
                        break
                    failures.append(f"{model}: HTTP {exc.code}")
                except httpx.TimeoutException:
                    failures.append(f"{model}: timeout")
                else:
                    return self._finish(response, model, phase, schema, started)
                if attempt + 1 < self._attempts:
                    self._sleep(self._backoff * (2**attempt))
        raise LLMUnavailable("; ".join(failures))

    def _finish(
        self,
        response: types.GenerateContentResponse,
        model: str,
        phase: str,
        schema: type[BaseModel] | None,
        started: float,
    ) -> LLMResponse:
        usage = response.usage_metadata
        entry = CostEntry(
            phase=phase,
            model=model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            thinking_tokens=(usage.thoughts_token_count or 0) if usage else 0,
            cached_tokens=(usage.cached_content_token_count or 0) if usage else 0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        self._meter.record(entry)
        text = response.text or ""
        parsed = None
        if schema is not None:
            parsed = response.parsed if isinstance(response.parsed, schema) else None
            if parsed is None:
                try:
                    parsed = schema.model_validate_json(text)
                except ValidationError:
                    parsed = None  # caller decides how to repair or fail
        return LLMResponse(text=text, parsed=parsed, model=model, usage=entry)
