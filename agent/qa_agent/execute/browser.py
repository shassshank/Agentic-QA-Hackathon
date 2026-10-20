"""Playwright harness: one fresh browser context per scenario, resilient waits, semantic actions."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .evidence import EvidenceRecorder

DESKTOP = {"width": 1280, "height": 800}
# Generic busy signals: ARIA busy regions and live status regions announcing work in progress.
_BUSY_JS = r"""() => {
  if (document.querySelector('[aria-busy="true"]')) return false;
  const busy = /\b(loading|checking|submitting|saving|processing|entering|logging out)\b/i;
  return ![...document.querySelectorAll('[role="status"]')].some(el => busy.test(el.textContent || ''));
}"""


class ActionError(RuntimeError):
    """A UI action could not be performed; carries what was attempted for evidence and recovery."""

    def __init__(self, action: str, target: str, reason: str) -> None:
        super().__init__(f"{action} {target!r}: {reason}")
        self.action, self.target, self.reason = action, target, reason


class ScenarioRun:
    def __init__(self, page: Page, recorder: EvidenceRecorder, base_url: str, timeout_ms: int) -> None:
        self.page = page
        self.recorder = recorder
        self.base_url = base_url
        self.timeout_ms = timeout_ms

    # --- waiting -------------------------------------------------------------------------------

    def settle(self, timeout_ms: int | None = None) -> bool:
        """Wait for network quiet and no busy indicators. Returns False if the UI never settled."""
        timeout = timeout_ms or self.timeout_ms
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except PlaywrightTimeout:
            pass  # long-polling or slow API: fall through to the busy-indicator check
        try:
            self.page.wait_for_function(_BUSY_JS, timeout=timeout)
            return True
        except PlaywrightTimeout:
            return False

    # --- navigation ----------------------------------------------------------------------------

    def goto(self, path: str, *, shot: bool = False) -> None:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except PlaywrightError as exc:
            self.recorder.step(self.page, "goto", target=path, ok=False, detail=str(exc).splitlines()[0])
            raise ActionError("goto", path, str(exc).splitlines()[0]) from exc
        settled = self.settle()
        self.recorder.step(self.page, "goto", target=path, ok=True, shot=shot,
                           detail=None if settled else "page did not settle within timeout")

    def reload(self, *, shot: bool = False) -> None:
        self.page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.settle()
        self.recorder.step(self.page, "reload", ok=True, shot=shot)

    def path(self) -> str:
        return re.sub(r"^https?://[^/]+", "", self.page.url) or "/"

    # --- actions -------------------------------------------------------------------------------

    def _visible(self, locator: Locator, action: str, target: str) -> Locator:
        try:
            locator.first.wait_for(state="visible", timeout=self.timeout_ms)
        except PlaywrightTimeout as exc:
            self.recorder.step(self.page, action, target=target, ok=False, detail="element not visible", shot=True)
            raise ActionError(action, target, "element not visible") from exc
        return locator.first

    def click(self, role: str, name: str | re.Pattern[str], *, shot: bool = False, settle: bool = True) -> None:
        target = f"{role}:{name if isinstance(name, str) else name.pattern}"
        element = self._visible(self.page.get_by_role(role, name=name), "click", target)  # type: ignore[arg-type]
        element.click(timeout=self.timeout_ms)
        if settle:
            self.settle()
        self.recorder.step(self.page, "click", target=target, ok=True, shot=shot)

    def fill(self, label: str | re.Pattern[str], value: str, *, shot: bool = False) -> None:
        target = label if isinstance(label, str) else label.pattern
        element = self._visible(self.page.get_by_label(label), "fill", target)
        element.fill(value, timeout=self.timeout_ms)
        self.recorder.step(self.page, "fill", target=target, value=value, ok=True, shot=shot)

    def select(self, label: str | re.Pattern[str], *, option_label: str | None = None,
               option_value: str | None = None, shot: bool = False) -> None:
        target = label if isinstance(label, str) else label.pattern
        element = self._visible(self.page.get_by_label(label), "select", target)
        if option_label is not None:
            # Option text may carry extra detail (e.g. balance), so match by substring.
            options = element.locator("option").all_inner_texts()
            match = next((o for o in options if option_label in o), None)
            if match is None:
                self.recorder.step(self.page, "select", target=target, value=option_label, ok=False,
                                   detail=f"no option containing {option_label!r}; options={options}", shot=True)
                raise ActionError("select", target, f"no option containing {option_label!r}")
            element.select_option(label=match, timeout=self.timeout_ms)
            value = match
        else:
            element.select_option(value=option_value or "", timeout=self.timeout_ms)
            value = option_value or ""
        self.recorder.step(self.page, "select", target=target, value=value, ok=True, shot=shot)

    def shot(self, label: str) -> str | None:
        return self.recorder.screenshot(self.page, label)

    # --- composite -----------------------------------------------------------------------------

    def enter_demo(self) -> None:
        """Start a fresh demo session from the landing page and wait for a protected view."""
        self.goto("/")
        self.click("button", re.compile(r"enter demo", re.I), settle=False)
        try:
            self.page.wait_for_url(re.compile(r"/dashboard"), timeout=self.timeout_ms)
        except PlaywrightTimeout as exc:
            self.recorder.step(self.page, "enter_demo", ok=False, detail="dashboard not reached", shot=True)
            raise ActionError("enter_demo", "/dashboard", "dashboard not reached after Enter Demo") from exc
        self.settle()
        self.recorder.step(self.page, "enter_demo", target="/dashboard", ok=True)


class Harness:
    def __init__(self, base_url: str, out_dir: Path, *, headless: bool = True, timeout_ms: int = 10_000,
                 trace: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.out_dir = out_dir
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.trace = trace
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> Harness:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    @contextmanager
    def scenario(self, scenario_id: str, *, viewport: dict[str, int] | None = None) -> Iterator[ScenarioRun]:
        assert self._browser is not None, "Harness must be entered before running scenarios"
        recorder = EvidenceRecorder(self.out_dir / scenario_id)
        context = self._browser.new_context(viewport=viewport or DESKTOP)
        if self.trace:
            context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        recorder.attach(page)
        try:
            yield ScenarioRun(page, recorder, self.base_url, self.timeout_ms)
        finally:
            if self.trace:
                context.tracing.stop(path=recorder.dir / "trace.zip")
            recorder.save()
            context.close()
