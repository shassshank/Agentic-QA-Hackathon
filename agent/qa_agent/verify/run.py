"""Pure orchestration; filesystem evidence is attached by the CLI before evaluation."""

from __future__ import annotations

from collections import Counter

from . import accounts, general, history, profile, seed, session, transfers
from .models import CheckReport
from .util import Builder


def evaluate(observations: dict) -> CheckReport:
    checks = []
    for probe in observations.get('results', []):
        b = Builder(probe)
        if probe.get('status') != 'completed':
            for behaviour in probe.get('behaviours') or ['probe.completed']:
                b.add(behaviour, 'unavailable', None, 'completed probe observations', probe.get('error'))
        else:
            for module in (session, accounts, transfers, history, profile, general, seed):
                module.evaluate(b)
            if not b.checks:
                b.add('probe.coverage', 'unavailable', None, 'facts for a supported check', probe.get('facts'))
        checks.extend(b.checks)
    counts = Counter(c.status for c in checks)
    # The observation timestamp makes repeated evaluation byte-for-byte deterministic.
    generated = observations.get('generated_at') or max(
        (p.get('started_at', '') for p in observations.get('results', [])), default='')
    return CheckReport(run_tag=observations.get('run_tag', ''), base_url=observations.get('base_url', ''),
                       generated_at=generated, checks=checks,
                       summary={s: counts[s] for s in ('pass', 'fail', 'unknown')})
