"""Integration tests against a running target (QA_TARGET_URL, default local DemoBank). Read-only flows."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from qa_agent.config import load_settings
from qa_agent.execute import ActionError, Harness

TARGET = load_settings().target_url


def _target_up() -> bool:
    try:
        return httpx.get(TARGET, timeout=3).is_success
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _target_up(), reason=f"target {TARGET} not reachable")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    with Harness(TARGET, tmp_path_factory.mktemp("runs")) as h:
        yield h


def test_enter_demo_records_steps_network_and_trace(harness):
    with harness.scenario("enter-demo") as run:
        run.enter_demo()
        cards = run.page.get_by_test_id("account-card")
        assert cards.count() == 2
        assert run.recorder.api_calls("POST", "/api/demo/session")[0].status == 201
        accounts = run.recorder.api_calls("GET", "/api/accounts")
        assert accounts and len(accounts[-1].response_body["accounts"]) == 2
        directory = run.recorder.dir
    evidence = json.loads((directory / "evidence.json").read_text())
    assert [s["action"] for s in evidence["steps"]][-1] == "enter_demo"
    assert (directory / "trace.zip").exists()


def test_missing_element_raises_action_error_with_screenshot(harness):
    with harness.scenario("missing") as run:
        run.timeout_ms = 1500
        run.goto("/")
        with pytest.raises(ActionError):
            run.click("button", re.compile("definitely not here"))
        failed = run.recorder.steps[-1]
        assert not failed.ok and failed.screenshot and (run.recorder.dir / failed.screenshot).exists()


def test_protected_route_without_session_returns_to_landing(harness):
    with harness.scenario("no-session") as run:
        run.goto("/dashboard")
        run.page.wait_for_url(re.compile(r"/$"), timeout=5000)
        assert run.path() == "/"
