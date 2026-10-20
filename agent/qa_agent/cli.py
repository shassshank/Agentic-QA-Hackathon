"""qa-agent command line entry point."""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config import load_settings

app = typer.Typer(add_completion=False, help="Specification-driven QA agent for DemoBank.")
console = Console()


@app.callback()
def main() -> None:
    """Specification-driven QA agent for DemoBank."""


def _check_target(url: str) -> tuple[bool, str]:
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
    except httpx.HTTPError as exc:
        return False, f"unreachable: {exc.__class__.__name__}"
    return response.is_success, f"HTTP {response.status_code}"


def _check_gemini(api_key: str, model: str) -> tuple[bool, str]:
    from google import genai
    from google.genai import types

    try:
        # Bounded wait: an overloaded model can hold the connection open instead of returning 503.
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))
        response = client.models.generate_content(model=model, contents="Reply with the single word: ok")
    except Exception as exc:  # noqa: BLE001 - surface any provider failure in the doctor table
        return False, f"{exc.__class__.__name__}: {str(exc)[:120]}"
    usage = response.usage_metadata
    tokens = f"{usage.prompt_token_count} in / {usage.candidates_token_count} out" if usage else "no usage"
    return True, f"replied {(response.text or '').strip()[:20]!r} ({tokens})"


def _check_browser(url: str) -> tuple[bool, str]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle", timeout=20_000)
            visible = page.get_by_role("button", name="Enter Demo").is_visible()
            title = page.title()
            browser.close()
    except PlaywrightError as exc:
        return False, f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:120]}"
    return visible, f"title {title!r}; Enter Demo button {'visible' if visible else 'not found'}"


@app.command()
def doctor(skip_llm: bool = typer.Option(False, help="Skip the live LLM call.")) -> None:
    """Check configuration, LLM access and target reachability."""
    settings = load_settings()
    table = Table(title="qa-agent doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")
    healthy = True

    def row(name: str, ok: bool, detail: str) -> None:
        nonlocal healthy
        healthy &= ok
        table.add_row(name, "[green]ok[/]" if ok else "[red]fail[/]", detail)

    key = settings.api_key_for(settings.provider)
    row("LLM provider", True, settings.provider)
    row("API key", key is not None, "present" if key else f"missing for {settings.provider}")
    if settings.provider == "gemini" and key and not skip_llm:
        for label, model in (("planner", settings.gemini_model_planner), ("fast", settings.gemini_model_fast)):
            ok, detail = _check_gemini(key.get_secret_value(), model)
            if not ok:
                # Mirror the runtime policy: an overloaded primary is acceptable if a fallback serves.
                for fallback in settings.gemini_model_fallbacks:
                    fb_ok, fb_detail = _check_gemini(key.get_secret_value(), fallback)
                    if fb_ok:
                        ok, detail = True, f"primary unavailable; fallback {fallback} {fb_detail}"
                        break
            row(f"Gemini {label} ({model})", ok, detail)
    ok, detail = _check_target(settings.target_url)
    row(f"Target {settings.target_url}", ok, detail)
    if ok:
        row("Browser (Chromium 1280x800)", *_check_browser(settings.target_url))

    console.print(table)
    raise typer.Exit(code=0 if healthy else 1)


if __name__ == "__main__":
    app()
