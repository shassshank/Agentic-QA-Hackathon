"""Per-scenario evidence: step log, screenshots, API traffic and console errors."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import ConsoleMessage, Error as PlaywrightError, Page, Response
from pydantic import BaseModel, Field

# Only the target's own API is evidence; static assets are noise.
API_MARKERS = ("/api/", "/health")
MAX_BODY_CHARS = 20_000


class NetworkRecord(BaseModel):
    method: str
    url: str
    status: int | None
    request_body: Any = None
    response_body: Any = None
    at: datetime
    duration_ms: int | None = None


class StepRecord(BaseModel):
    index: int
    action: str
    target: str | None = None
    value: str | None = None
    ok: bool
    url: str
    screenshot: str | None = None
    detail: str | None = None
    method: str | None = None
    score: float | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsoleRecord(BaseModel):
    type: str
    text: str


def _json_or_text(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw[:MAX_BODY_CHARS]


class EvidenceRecorder:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[StepRecord] = []
        self.network: list[NetworkRecord] = []
        self.console: list[ConsoleRecord] = []
        self._shots = 0

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)
        page.on("console", self._on_console)
        page.on("pageerror", lambda exc: self.console.append(ConsoleRecord(type="pageerror", text=str(exc))))

    def _on_response(self, response: Response) -> None:
        if not any(marker in response.url for marker in API_MARKERS):
            return
        request = response.request
        timing = request.timing
        try:
            body = _json_or_text(response.text())
        except PlaywrightError:
            body = None  # body unavailable (e.g. navigated away or 204)
        self.network.append(
            NetworkRecord(
                method=request.method,
                url=response.url,
                status=response.status,
                request_body=_json_or_text(request.post_data),
                response_body=body,
                at=datetime.now(UTC),
                duration_ms=int(timing["responseEnd"]) if timing and timing.get("responseEnd", -1) >= 0 else None,
            )
        )

    def _on_console(self, message: ConsoleMessage) -> None:
        if message.type in ("error", "warning"):
            self.console.append(ConsoleRecord(type=message.type, text=message.text[:2000]))

    def screenshot(self, page: Page, label: str) -> str | None:
        self._shots += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:60]
        name = f"{self._shots:02d}-{safe}.png"
        try:
            page.screenshot(path=self.dir / name, full_page=True)
        except PlaywrightError:
            return None
        return name

    def step(self, page: Page, action: str, *, ok: bool, target: str | None = None, value: str | None = None,
             detail: str | None = None, shot: bool = False, method: str | None = None,
             score: float | None = None) -> StepRecord:
        record = StepRecord(
            index=len(self.steps) + 1,
            action=action,
            target=target,
            value=value,
            ok=ok,
            url=page.url,
            screenshot=self.screenshot(page, f"{action}-{target or ''}") if shot else None,
            detail=detail,
            method=method,
            score=score,
        )
        self.steps.append(record)
        return record

    def api_calls(self, method: str | None = None, path_contains: str | None = None) -> list[NetworkRecord]:
        return [
            r for r in self.network
            if (method is None or r.method == method) and (path_contains is None or path_contains in r.url)
        ]

    def save(self) -> Path:
        path = self.dir / "evidence.json"
        payload = {
            "saved_at": datetime.now(UTC).isoformat(),
            "steps": [s.model_dump(mode="json") for s in self.steps],
            "network": [n.model_dump(mode="json") for n in self.network],
            "console": [c.model_dump(mode="json") for c in self.console],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return path


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
