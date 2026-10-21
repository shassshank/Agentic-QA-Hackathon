"""Assemble one JSON document with everything the audit report shows; renderers only format it."""
from __future__ import annotations

import base64
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..models import RequirementCatalogue
from .steps import describe, describe_recorded

REASONS = {
    'not_testable': 'The PRD statement cannot be checked from the browser.',
    'no_linked_checks': 'No automated check confidently covers this requirement.',
    'partial_coverage': 'Checks passed, but they cover only some of the requirement\'s assertions.',
    'checks_unknown': 'The linked checks ran but could not reach a definite result.',
    'needs_judgement': 'A wording check needs judgement and none was confident enough.',
    'flaky': 'The failure did not reproduce consistently on re-run.',
    'unconfirmed': 'A check failed but was not re-run to confirm it.',
    'probe_blocked': 'The scenario could not finish, so its checks are unknown.',
    'concurrent_activity': 'Other activity on the shared account interfered with the measurement.',
    'llm_unavailable': 'The decision service was unavailable, so this was not linked.',
    'link_disputed': 'A check failed, but the review found it does not violate this requirement.',
    'unreviewed': 'A failure was confirmed but could not be reviewed against this requirement with confidence.',
}
STATIC = re.compile(r'(\.(ts|tsx|js|jsx|css|svg|png|ico|woff2?|map)(\?|$))|/@|/node_modules/|/src/')
SHOT_WIDTH, SHOT_QUALITY = 1100, 72


def _short(value, limit=800) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + ' …'


def _caption(path: Path) -> str:
    words = re.sub(r'^\d+-', '', path.stem).replace('--', ' ').replace('-', ' ').strip()
    return words[:1].upper() + words[1:]


def _image(path: Path) -> str:
    """Downscaled JPEG data URI keeps the single-file report a few MB; falls back to the original bytes."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert('RGB')
            if img.width > SHOT_WIDTH:
                img = img.resize((SHOT_WIDTH, round(img.height * SHOT_WIDTH / img.width)))
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=SHOT_QUALITY, optimize=True)
            return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()


def _network(entries: list[dict], base_url: str) -> list[dict]:
    calls = []
    for e in entries:
        url = e.get('url') or ''
        if STATIC.search(url) or (url.startswith(base_url) and e.get('method') == 'GET' and '/api/' not in url):
            continue
        path = re.sub(r'^https?://[^/]+', '', url)
        calls.append(dict(method=e.get('method'), path=path, status=e.get('status'),
                          request=_short(e.get('request_body'), 300) if e.get('request_body') else None,
                          response=_short(e.get('response_body'), 300) if e.get('response_body') else None))
    return calls[:40]


def probe_section(result: dict, run_dir: Path, base_url: str, shots: bool) -> dict:
    folder = run_dir / 'evidence' / result['id']
    evidence = {}
    try:
        evidence = json.loads((folder / 'evidence.json').read_text())
    except (OSError, ValueError):
        pass
    steps = [dict(n=s.get('index'), text=describe(s.get('action', ''), s.get('target'), s.get('value')),
                  ok=s.get('ok', True), detail=s.get('detail')) for s in evidence.get('steps', [])]
    # A screenshot taken by a step is captioned with that step's plain words; file names can carry locator patterns.
    taken = {Path(s['screenshot']).name: f"After: {describe(s.get('action', ''), s.get('target'), s.get('value'))}"
             for s in evidence.get('steps', []) if s.get('screenshot')}
    pictures = [dict(name=p.name, caption=taken.get(p.name, _caption(p)), src=_image(p))
                for p in sorted(folder.glob('*.png'))] if shots else []
    return dict(id=result['id'], title=result.get('title'), area=result.get('area'), kind=result.get('kind'),
                status=result.get('status'), error=result.get('error'), duration_ms=result.get('duration_ms'),
                mutating=result.get('mutating'), steps=steps, screenshots=pictures,
                network=_network(evidence.get('network', []), base_url),
                console=[_short(c, 300) for c in evidence.get('console', [])][:20],
                recoveries=(result.get('facts') or {}).get('_recoveries', []))


def cost_section(run_dir: Path, verdicts: dict) -> dict:
    """Prefer the full meter (per-call models); verdicts.json only has per-phase totals."""
    models: dict[str, set] = defaultdict(set)
    try:
        meter = json.loads((run_dir / 'cost.json').read_text())
    except (OSError, ValueError):
        meter = None
    if meter:
        for entry in meter.get('entries', []):
            models[entry['phase']].add(entry['model'])
    phases = meter.get('by_phase', {}) if meter else verdicts.get('cost', {})
    rows, total, unpriced = [], 0.0, False
    for phase, row in phases.items():
        names = models.get(phase, set())
        provider = ('typesafe' if any(m.startswith('jev') for m in names) else
                    'gemini' if any(m.startswith('gemini') for m in names) else
                    'gemini' if phase.endswith('_escalation') or phase == 'interpret' else
                    'typesafe' if phase in ('link', 'judge', 'review', 'heal') else '')
        cost = row.get('cost_usd')
        unpriced |= cost is None and bool(row.get('calls'))
        total += cost or 0.0
        rows.append(dict(phase=phase, provider=provider, models=sorted(names), calls=row.get('calls', 0),
                         input_tokens=row.get('input_tokens', 0), output_tokens=row.get('output_tokens', 0),
                         cost_usd=cost))
    return dict(phases=rows, total_usd=round(total, 6), complete=not unpriced,
                note='Some calls use models without a configured price; the total is a lower bound.' if unpriced else None)


def build(run_dir: Path, catalogue: RequirementCatalogue, *, screenshots: bool = True) -> dict:
    verdicts = json.loads((run_dir / 'verdicts.json').read_text())
    checks = json.loads((run_dir / 'checks.json').read_text()).get('checks', [])
    observations = json.loads((run_dir / 'observations.json').read_text())
    base_url = observations.get('base_url', '')
    by_check = {(c['probe_id'], c['id']): c for c in checks}
    requirements = {r.id: r for r in catalogue.requirements}
    results = observations.get('results', [])
    probes = {r['id']: probe_section(r, run_dir, base_url, screenshots) for r in results}
    rows = []
    for v in verdicts['verdicts']:
        req = requirements.get(v['requirement_id'])
        linked = []
        for c in v.get('checks', []):
            full = by_check.get((c.get('probe_id'), c.get('id')), {})
            linked.append(dict(id=c.get('id'), probe_id=c.get('probe_id'), status=c.get('status'),
                               confirmed=c.get('confirmed'), expected=full.get('expected'),
                               observed=_short(full.get('observed')) if full.get('observed') is not None else None,
                               reason=full.get('reason')))
        failure = None
        if v.get('failure'):
            f = v['failure']
            failure = dict(probe_id=f.get('probe_id'), check_id=f.get('check_id'),
                           steps=[describe_recorded(s) for s in f.get('steps', [])],
                           checks=[dict(check_id=c.get('check_id'), probe_id=c.get('probe_id'), expected=c.get('expected'),
                                        observed=_short(c.get('observed'), 600)) for c in (f.get('checks') or [f])[:12]])
        rows.append(dict(
            id=v['requirement_id'], area=v.get('area'), title=v.get('title'), status=v['status'], reason=v.get('reason'),
            reason_text=REASONS.get(v.get('reason') or ''), coverage=v.get('coverage'),
            statement=req.statement if req else None, source_quote=req.source_quote if req else None,
            source_bullets=list(req.source_bullets) if req else [],
            testability=str(getattr(req.testability, 'value', req.testability)) if req else None,
            assertions=[a.description for a in req.assertions] if req else [],
            review=v.get('review'), checks=linked, failure=failure,
            probes=sorted({c['probe_id'] for c in linked if c.get('probe_id')})))
    by_area: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_area[r['area']][r['status']] += 1
    counts = Counter(r['status'] for r in rows)
    return dict(
        meta=dict(title='DemoBank QA audit', target_url=base_url, run_tag=verdicts.get('run_tag'),
                  generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  catalogue=verdicts.get('catalogue', {}), probes_total=len(results),
                  probes_completed=sum(1 for r in results if r.get('status') == 'completed'),
                  probe_runtime_s=round(sum(r.get('duration_ms') or 0 for r in results) / 1000, 1)),
        summary=dict(total=len(rows), passed=counts['passed'], failed=counts['failed'], unverified=counts['unverified'],
                     coverage=dict(Counter(r['coverage'] for r in rows)),
                     reasons=dict(Counter(r['reason'] for r in rows if r['reason'])),
                     by_area={a: dict(passed=c['passed'], failed=c['failed'], unverified=c['unverified'])
                              for a, c in by_area.items()}),
        reasons=REASONS, requirements=rows, probes=probes, cost=cost_section(run_dir, verdicts),
        warnings=verdicts.get('warnings', []))
