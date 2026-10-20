"""qa-agent command line entry point."""

from __future__ import annotations

from pathlib import Path

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


@app.command("interpret")
def interpret_command(
    prd: Path = typer.Option(..., exists=True, dir_okay=False, help="Path to the PRD markdown."),
    out: Path = typer.Option(Path("runs/requirements.json"), help="Where to write the catalogue."),
    no_cache: bool = typer.Option(False, help="Ignore the cached catalogue for this PRD and model."),
) -> None:
    """Extract atomic, verifiable requirements from the PRD."""
    from collections import Counter

    from .config import AGENT_ROOT
    from .interpret import interpret, save
    from .llm import CostMeter, get_client

    settings = load_settings()
    meter = CostMeter()
    catalogue = interpret(prd, get_client(settings, meter), cache_dir=AGENT_ROOT / ".cache", use_cache=not no_cache)
    save(catalogue, out)

    table = Table(title=f"{len(catalogue.requirements)} requirements from {len(catalogue.bullets)} PRD bullets")
    table.add_column("Area")
    table.add_column("Count", justify="right")
    for area, count in sorted(Counter(r.area.value for r in catalogue.requirements).items()):
        table.add_row(area, str(count))
    console.print(table)
    testability = Counter(r.testability.value for r in catalogue.requirements)
    console.print(f"Testability: {dict(testability)}  |  excluded bullets: {len(catalogue.excluded)}")
    console.print(f"Model: {catalogue.model}  |  warnings: {len(catalogue.warnings)}")
    for warning in catalogue.warnings:
        console.print(f"  [yellow]! {warning}[/]")
    if meter.entries:
        for entry in meter.entries:
            console.print(
                f"LLM {entry.phase}: {entry.model} in={entry.input_tokens} out={entry.output_tokens} "
                f"thinking={entry.thinking_tokens} {entry.latency_ms} ms"
            )
        meter.save(out.with_name("cost_interpret.json"))
    else:
        console.print("LLM: cache hit, 0 tokens")
    console.print(f"Wrote {out}")


@app.command("probe")
def probe_command(
    url: str = typer.Option(None, help="Target URL (default: QA_TARGET_URL)."),
    out: Path = typer.Option(None, help="Run directory (default: runs/<timestamp>)."),
    only: list[str] = typer.Option(None, help="Probe id or prefix (repeatable), e.g. trf.reject or ses."),
    skip_mutating: bool = typer.Option(False, help="Skip probes that move money."),
    headed: bool = typer.Option(False, help="Show the browser."),
    chaos: str = typer.Option("", help="Comma-separated labels,testids,slow,flaky variants."),
    list_only: bool = typer.Option(False, "--list", help="List probes and exit."),
) -> None:
    """Run deterministic scenarios against the target and record observations (no LLM)."""
    from datetime import datetime

    from .plan import ProbeResult, run_probes, select_probes

    probes = select_probes(only, skip_mutating)
    if list_only:
        table = Table(title=f"{len(probes)} probes")
        for column in ("Id", "Area", "Kind", "Mutating", "Title"):
            table.add_column(column)
        for p in probes:
            table.add_row(p.id, p.area.value, p.kind.value, "yes" if p.mutating else "", p.title)
        console.print(table)
        return

    settings = load_settings()
    target = (url or settings.target_url).rstrip("/")
    run_dir = out or settings.runs_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    colours = {"completed": "green", "blocked": "yellow", "errored": "red"}

    def report(result: ProbeResult) -> None:
        detail = f" — {result.error.splitlines()[0]}" if result.error else ""
        console.print(f"[{colours[result.status]}]{result.status:9}[/] {result.id:32} {result.duration_ms:6} ms{detail}")

    results = run_probes(target, run_dir, only=only, skip_mutating=skip_mutating, headless=not headed,
                         on_result=report, chaos=[v.strip() for v in chaos.split(",") if v.strip()])
    restored = [r.facts.get("restore", {}).get("restored") for r in results if "restore" in r.facts]
    console.print(f"{len(results)} probes: " + ", ".join(
        f"{s}={sum(r.status == s for r in results)}" for s in ("completed", "blocked", "errored")))
    if restored:
        console.print(f"Net-zero restore: {sum(bool(x) for x in restored)}/{len(restored)} probes back at baseline")
    console.print(f"Wrote {run_dir / 'observations.json'}")


@app.command("verify")
def verify_command(
    run: Path = typer.Option(..., exists=True, file_okay=False, help="Recorded run directory."),
) -> None:
    """Evaluate recorded facts without driving the app or calling a model."""
    import json
    from collections import Counter

    from .verify import evaluate

    observations = json.loads((run / "observations.json").read_text())
    for probe in observations.get("results", []):
        evidence_path = run / "evidence" / probe["id"] / "evidence.json"
        if evidence_path.exists():
            bundle = json.loads(evidence_path.read_text())
            reference = f"evidence/{probe['id']}/evidence.json"
            probe["_evidence"] = {
                "screenshots": sorted({step["screenshot"] for step in bundle.get("steps", [])
                                       if step.get("screenshot")}),
                "steps": [f"{reference}#/steps/{i}" for i, _ in enumerate(bundle.get("steps", []))],
                "network": [f"{reference}#/network/{i}" for i, _ in enumerate(bundle.get("network", []))],
            }
    report = evaluate(observations)
    output = run / "checks.json"
    output.write_text(report.model_dump_json(indent=2))
    table = Table(title="Oracle checks")
    for column in ("Area", "Pass", "Fail", "Unknown"):
        table.add_column(column)
    for area in sorted({c.area for c in report.checks}):
        counts = Counter(c.status for c in report.checks if c.area == area)
        table.add_row(area, *(str(counts[s]) for s in ("pass", "fail", "unknown")))
    console.print(table)
    for check in report.checks:
        if check.status == "fail":
            console.print(f"FAIL {check.probe_id} / {check.id}: {check.reason}", markup=False)
    console.print(f"Wrote {output}")


@app.command("chaos-compare")
def chaos_compare_command(base: Path = typer.Option(..., exists=True),
                          variant: Path = typer.Option(..., exists=True)) -> None:
    """Report verdict changes between a baseline and a chaos run."""
    import json
    from .chaos.compare import compare

    result = compare(base, variant)
    (variant / "compare.json").write_text(json.dumps(result, indent=2))
    console.print(result['transitions'])
    console.print(f"Pass/fail flips: {len(result['flips'])}")


def _print_verdicts(report) -> None:
    table = Table(title="Requirement verdicts")
    for column in ("Requirement", "Status", "Reason"):
        table.add_column(column)
    for verdict in report.verdicts:
        table.add_row(verdict.requirement_id, verdict.status, verdict.reason or "")
    console.print(table)
    console.print(str(report.summary))


def _decider(value, settings, meter, factory):
    if value not in ('jev', 'none'):
        raise typer.BadParameter('--decider must be jev or none')
    return factory(settings, meter) if value == 'jev' else None


class _CachedInterpreter:
    def __init__(self, settings):
        self.provider = settings.provider
        self.settings = settings

    def model_for(self, tier):
        return self.settings.gemini_model_planner

    def generate(self, *args, **kwargs):
        raise RuntimeError('Interpretation cache miss; Gemini is required for interpretation')


@app.command("verdict")
def verdict_command(
    run: Path = typer.Option(..., exists=True, file_okay=False),
    catalogue: Path = typer.Option(..., exists=True, dir_okay=False),
    no_judge: bool = typer.Option(False, help="Keep wording checks unknown; no judge call."),
    no_confirm: bool = typer.Option(False, help="Skip probe re-runs; failures remain unconfirmed."),
    decider: str = typer.Option("jev", hidden=True),
    link_file: Path = typer.Option(None, hidden=True, exists=True, dir_okay=False),
) -> None:
    """Turn existing checks into requirement verdicts."""
    from .config import AGENT_ROOT
    from .llm import CostMeter, get_client, get_decider
    from .models import RequirementCatalogue
    from .verdict import run_verdict
    from .verdict.models import LinkResult

    settings = load_settings()
    meter = CostMeter()
    decision_client = _decider(decider, settings, meter, get_decider)
    client = None
    if link_file is None or not no_judge:
        try:
            client = get_client(settings, meter)
        except Exception as exc:
            console.print(f"LLM unavailable: {exc}", markup=False)
    result = run_verdict(
        run, RequirementCatalogue.model_validate_json(catalogue.read_text()), client,
        cache_dir=AGENT_ROOT / ".cache", meter=meter, decider=decision_client,
        link_result=LinkResult.model_validate_json(link_file.read_text()) if link_file else None,
        no_judge=no_judge, no_confirm=no_confirm)
    _print_verdicts(result)
    console.print(f"Wrote {run / 'verdicts.json'}")


@app.command("run")
def run_command(
    prd: Path = typer.Option(..., exists=True, dir_okay=False),
    url: str = typer.Option(...),
    out: Path = typer.Option(None, help="Run directory (default: runs/<timestamp>)."),
    no_confirm: bool = typer.Option(False),
    decider: str = typer.Option("jev", hidden=True),
) -> None:
    """Interpret, probe, verify and produce requirement verdicts."""
    from datetime import datetime
    from .config import AGENT_ROOT
    from .interpret import interpret, save
    from .llm import CostMeter, get_client, get_decider
    from .verdict import run_verdict

    settings = load_settings()
    meter = CostMeter()
    decision_client = _decider(decider, settings, meter, get_decider)
    try:
        client = get_client(settings, meter)
    except Exception as exc:
        if decision_client is None:
            raise
        console.print(f"LLM unavailable: {exc}", markup=False)
        client = _CachedInterpreter(settings)
    run_dir = out or settings.runs_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    catalogue = interpret(prd, client, cache_dir=AGENT_ROOT / ".cache")
    save(catalogue, run_dir / "requirements.json")
    probe_command(url=url, out=run_dir, only=None, skip_mutating=False, headed=False, list_only=False, chaos="")
    verify_command(run=run_dir)
    verdict_client = None if isinstance(client, _CachedInterpreter) else client
    result = run_verdict(run_dir, catalogue, verdict_client, cache_dir=AGENT_ROOT / ".cache",
                         meter=meter, decider=decision_client, no_confirm=no_confirm)
    _print_verdicts(result)


if __name__ == "__main__":
    app()
