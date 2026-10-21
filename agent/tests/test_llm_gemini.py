from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from qa_agent.llm.base import LLMUnavailable
from qa_agent.llm.gemini import GeminiClient
from qa_agent.llm.meter import CostMeter


class FakeModels:
    def __init__(self, script: dict[str, list]) -> None:
        self.script = script
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok(text: str = "ok"):
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=2, thoughts_token_count=5,
                            cached_content_token_count=0)
    return SimpleNamespace(text=text, parsed=None, usage_metadata=usage)


def _client(script: dict[str, list], fallbacks: list[str]) -> tuple[GeminiClient, FakeModels, CostMeter]:
    meter = CostMeter()
    client = GeminiClient("test-key", planner_model="primary", fast_model="fast", fallbacks=fallbacks,
                          meter=meter, sleep=lambda _s: None)
    models = FakeModels(script)
    client._clients = {"planner": SimpleNamespace(models=models), "fast": SimpleNamespace(models=models)}
    return client, models, meter


def test_overloaded_then_retired_then_served():
    client, models, meter = _client(
        {
            "primary": [errors.ServerError(503, {"error": {"message": "busy"}}), httpx.ReadTimeout("slow")],
            "retired": [errors.ClientError(404, {"error": {"message": "gone"}})],
            "backup": [_ok("served")],
        },
        fallbacks=["retired", "backup"],
    )
    response = client.generate("hi", phase="test", tier="planner")
    assert response.text == "served" and response.model == "backup"
    assert models.calls == ["primary", "primary", "retired", "backup"]  # 404 is not retried
    assert [(e.phase, e.model, e.thinking_tokens) for e in meter.entries] == [("test", "backup", 5)]


def test_fallback_without_thinking_support_is_retried_without_it():
    configs = []
    client, models, _ = _client(
        {"fast": [errors.ServerError(503, {})] * 2,
         "older": [errors.ClientError(400, {"error": {"message": "Thinking level MINIMAL is not supported"}}),
                   _ok("served")]},
        fallbacks=["older"],
    )
    original = models.generate_content
    def spy(*, model, contents, config):
        configs.append((model, config.thinking_config))
        return original(model=model, contents=contents, config=config)
    models.generate_content = spy
    response = client.generate("hi", phase="review", tier="fast", thinking="minimal")
    assert response.model == "older"
    assert [c[1] is None for c in configs] == [False, False, False, True]  # dropped only after the 400
    other = errors.ClientError(400, {"error": {"message": "bad schema"}})
    client, _, _ = _client({"primary": [other]}, fallbacks=[])
    with pytest.raises(errors.ClientError):  # unrelated 400s still surface
        client.generate("hi", phase="review", tier="planner", thinking="minimal")


def test_all_models_failing_raises_with_reasons():
    client, _, _ = _client(
        {"primary": [errors.ServerError(503, {})] * 2, "backup": [errors.ServerError(429, {})] * 2},
        fallbacks=["backup"],
    )
    with pytest.raises(LLMUnavailable, match="primary: HTTP 503.*backup: HTTP 429"):
        client.generate("hi", phase="test", tier="planner")


def test_daily_quota_429_skips_retry_and_moves_on():
    quota = {"error": {"message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "53540s"}]}}
    client, models, _ = _client(
        {"primary": [errors.ClientError(429, quota)], "backup": [_ok()]}, fallbacks=["backup"]
    )
    assert client.generate("hi", phase="test", tier="planner").model == "backup"
    assert models.calls == ["primary", "backup"]


def test_short_retry_delay_is_honoured():
    quota = {"error": {"message": "slow down", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}}
    slept: list[float] = []
    client, _, _ = _client({"primary": [errors.ClientError(429, quota), _ok()]}, fallbacks=[])
    client._sleep = slept.append
    assert client.generate("hi", phase="test", tier="planner").model == "primary"
    assert slept == [30.0]


def test_non_retryable_client_error_propagates():
    client, _, _ = _client({"primary": [errors.ClientError(400, {"error": {"message": "bad"}})]}, fallbacks=[])
    with pytest.raises(errors.ClientError):
        client.generate("hi", phase="test", tier="planner")
