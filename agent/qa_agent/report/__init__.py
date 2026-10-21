"""Audit report: one data document, rendered as a self-contained HTML page and a Markdown summary."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import RequirementCatalogue
from .data import build

TEMPLATE = Path(__file__).with_name('template.html')
PLACEHOLDER = '/*__REPORT_DATA__*/'


def render_html(data: dict, template: str | None = None) -> str:
    template = template if template is not None else TEMPLATE.read_text()
    if PLACEHOLDER not in template:
        raise ValueError(f'report template has no {PLACEHOLDER} placeholder')
    # Inside <script type="application/json"> "</" can end the element and "<!--" can change how the parser reads
    # it; both escapes stay valid JSON ("\/" and "!" decode back to "/" and "!").
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/').replace('<!--', '<\\u0021--')
    return template.replace(PLACEHOLDER, payload)


def render_markdown(data: dict) -> str:
    s, m = data['summary'], data['meta']
    lines = [f"# {m['title']}", '',
             f"Target `{m['target_url']}` · run `{m['run_tag']}` · generated {m['generated_at']}", '',
             f"**{s['passed']} passed · {s['failed']} failed · {s['unverified']} unverified** of {s['total']} requirements "
             f"({m['probes_completed']}/{m['probes_total']} scenarios completed).", '']
    failed = [r for r in data['requirements'] if r['status'] == 'failed']
    if failed:
        lines += ['## Failed', '']
        for r in failed:
            lines += [f"### {r['id']} — {r['title']}", '', f"> {r['source_quote'] or r['statement']}", '']
            for c in (r['failure'] or {}).get('checks', [])[:4]:
                lines.append(f"- Expected: {c['expected']} — observed: `{(c['observed'] or '')[:160]}`")
            if r['failure'] and r['failure']['steps']:
                lines += ['', 'Steps to reproduce:', ''] + [f"{i}. {t}" for i, t in enumerate(r['failure']['steps'], 1)]
            lines.append('')
    lines += ['## All requirements', '', '| ID | Title | Status | Why |', '|---|---|---|---|']
    for r in data['requirements']:
        lines.append(f"| {r['id']} | {r['title']} | {r['status']} | {r['reason_text'] or ''} |")
    c = data['cost']
    usd = lambda v: 'unpriced' if v is None else f'{v:.6f}'
    lines += ['', '## Cost', '',
              '| Phase | Provider | Calls | Cached | Input tokens | Output tokens | This run (USD) | Cold run (USD) |',
              '|---|---|---|---|---|---|---|---|']
    for p in c['phases']:
        lines.append(f"| {p['phase']} | {p['provider']} | {p['calls']} | {p.get('cached_calls', 0)} | "
                     f"{p['input_tokens']} | {p.get('output_tokens', 0)} | {usd(p['cost_usd'])} | "
                     f"{usd(p.get('cold_cost_usd', p['cost_usd']))} |")
    lines += ['', f"This run: ${c['total_usd']:.6f} · cold run (no cache): ${c.get('cold_total_usd', c['total_usd']):.6f}"
              + (f" ({c['note']})" if c['note'] else ''), '']
    return '\n'.join(lines)


def write_report(run_dir: Path, catalogue: RequirementCatalogue, *, screenshots: bool = True) -> dict[str, Path]:
    data = build(run_dir, catalogue, screenshots=screenshots)
    paths = {'html': run_dir / 'report.html', 'md': run_dir / 'report.md', 'json': run_dir / 'report.json'}
    paths['html'].write_text(render_html(data))
    paths['md'].write_text(render_markdown(data))
    lean = {**data, 'probes': {k: {**p, 'screenshots': [{'name': s['name'], 'caption': s['caption']}
                                                        for s in p['screenshots']]} for k, p in data['probes'].items()}}
    paths['json'].write_text(json.dumps(lean, indent=2, ensure_ascii=False))
    return paths
