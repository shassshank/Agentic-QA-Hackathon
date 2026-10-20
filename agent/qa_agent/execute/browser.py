"""Playwright harness: one fresh browser context per scenario, resilient waits, semantic actions."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .evidence import EvidenceRecorder

DESKTOP = {"width": 1280, "height": 800}
QUIET_MS = 300
# Generic busy signals: ARIA busy regions and live status regions announcing work in progress.
_BUSY_JS = r"""() => {
  if (document.querySelector('[aria-busy="true"]')) return false;
  // Short announcements that start with a busy verb or end in an ellipsis ("Loading accounts…").
  // Long status regions (receipts, result counts) are content, even if they contain "Checking".
  const busyStart = /^(loading|checking|submitting|saving|processing|entering|logging out|please wait|signing)\b/i;
  const ellipsis = /(…|\.\.\.)$/;
  return ![...document.querySelectorAll('[role="status"]')].some(el => {
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
    return text.length > 0 && text.length <= 80 && (busyStart.test(text) || ellipsis.test(text));
  });
}"""
# Browser-clock timeline of API requests and feedback messages, so ordering claims ("success only after
# commit", "loading state while waiting", "no duplicate submit") are judged on one clock.
TIMELINE_JS = r"""(() => {
  if (window.__qa) return;
  const log = window.__qa = { events: [], inflight: 0, last: Date.now() };
  const push = (e) => {
    const t = Date.now();
    if (e.kind === 'request') log.inflight++;
    else if (e.kind === 'response' || e.kind === 'request-failed') log.inflight = Math.max(0, log.inflight - 1);
    if (e.kind !== 'feedback') log.last = t;
    log.events.push({ ...e, t });
  };
  const origFetch = window.fetch;
  window.fetch = async (...args) => {
    const req = args[0];
    const url = String((req && req.url) || req);
    const method = String((args[1] && args[1].method) || (req && req.method) || 'GET').toUpperCase();
    push({ kind: 'request', url, method });
    try {
      const res = await origFetch(...args);
      push({ kind: 'response', url, method, status: res.status });
      return res;
    } catch (err) {
      push({ kind: 'request-failed', url, method });
      throw err;
    }
  };
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    const m = String(method).toUpperCase(), u = String(url);
    this.addEventListener('loadstart', () => push({ kind: 'request', url: u, method: m }));
    this.addEventListener('loadend', () => push({ kind: 'response', url: u, method: m, status: this.status }));
    return origOpen.call(this, method, url, ...rest);
  };
  const seen = new WeakMap();
  const scan = () => {
    for (const el of document.querySelectorAll('[role="alert"],[role="status"],[aria-live]')) {
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text && seen.get(el) !== text) {
        seen.set(el, text);
        push({ kind: 'feedback', role: el.getAttribute('role') || 'live', text: text.slice(0, 300) });
      }
    }
  };
  const start = () => new MutationObserver(scan).observe(document.documentElement,
    { subtree: true, childList: true, characterData: true });
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
})();"""


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

    def settle(self, timeout_ms: int | None = None, quiet_ms: int = QUIET_MS) -> bool:
        """Wait until no API request is in flight, nothing started for quiet_ms, and no busy indicator.

        Playwright's "networkidle" load state only fires once per document, so after client-side
        navigation it returns immediately; the in-page request counter covers SPA transitions. The quiet
        window is measured from the call, giving click handlers and effects time to start their fetches.
        """
        timeout = timeout_ms or self.timeout_ms
        try:
            self.page.wait_for_function(
                f"""([since, quiet]) => {{
                  const q = window.__qa;
                  if (!document.body) return false;
                  if (q && (q.inflight > 0 || Date.now() - Math.max(q.last, since) < quiet)) return false;
                  if (!q && Date.now() - since < quiet) return false;
                  return ({_BUSY_JS})();
                }}""",
                arg=[int(time.time() * 1000), quiet_ms],
                timeout=timeout,
                polling=50,
            )
            return True
        except PlaywrightTimeout:
            return False
        except PlaywrightError:
            return False  # page navigated mid-wait; callers re-check state explicitly

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

    def submit(self, name: str | re.Pattern[str], *, shot: bool = False, settle: bool = True,
               clicks: int = 1) -> None:
        """Press a form's submit button by its accessible name, falling back to the form's submit control."""
        target = f"button:{name if isinstance(name, str) else name.pattern}"
        named = self.page.get_by_role("button", name=name)  # type: ignore[arg-type]
        locator = named if named.count() else self.page.locator("form [type=submit]")
        element = self._visible(locator, "submit", target)
        if clicks > 1:
            # Rapid repeated clicks without waiting: probes duplicate-submission handling.
            for _ in range(clicks):
                element.click(timeout=self.timeout_ms, force=True, no_wait_after=True)
        else:
            element.click(timeout=self.timeout_ms)
        if settle:
            self.settle()
        self.recorder.step(self.page, "submit", target=target, value=f"clicks={clicks}" if clicks > 1 else None,
                           ok=True, shot=shot)

    def shot(self, label: str) -> str | None:
        return self.recorder.screenshot(self.page, label)

    # --- browser-side timeline -----------------------------------------------------------------

    def timeline(self) -> list[dict]:
        """Events since the last full page load (SPA navigation keeps them)."""
        try:
            return self.page.evaluate("() => (window.__qa && window.__qa.events) || []")
        except PlaywrightError:
            return []

    def clear_timeline(self) -> None:
        try:
            self.page.evaluate("() => { if (window.__qa) window.__qa.events = []; }")
        except PlaywrightError:
            pass

    def throttle(self, latency_ms: int) -> None:
        """Add network latency (Chromium CDP) so transient loading states become observable."""
        # Conditions belong to the CDP session that set them, so reuse one session to be able to lift them.
        session = self.__dict__.get("_cdp")
        if session is None:
            session = self.__dict__["_cdp"] = self.page.context.new_cdp_session(self.page)
            session.send("Network.enable")
        session.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": latency_ms, "downloadThroughput": -1, "uploadThroughput": -1,
        })
        self.recorder.step(self.page, "throttle", value=f"{latency_ms}ms", ok=True)

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
        tracing = False
        try:
            context.add_init_script(TIMELINE_JS)
            if self.trace:
                context.tracing.start(screenshots=True, snapshots=True)
                tracing = True
            page = context.new_page()
            recorder.attach(page)
            yield ScenarioRun(page, recorder, self.base_url, self.timeout_ms)
        finally:
            # Evidence persistence is best effort; closing the context is not.
            try:
                if tracing:
                    context.tracing.stop(path=recorder.dir / "trace.zip")
            except PlaywrightError:
                pass
            finally:
                try:
                    recorder.save()
                finally:
                    context.close()
