"""Apply conservative precedence to prevent incomplete coverage from passing."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
from ..models import Testability
from .link import matches
from .models import Verdict


def failure_details(check, run_dir: Path | None):
    steps = list(check.evidence.steps)
    if run_dir:
        path = run_dir / 'evidence' / check.probe_id / 'evidence.json'
        if path.exists():
            bundle = json.loads(path.read_text())
            steps = [step.get('description') or ' '.join(str(step[k]) for k in ('action', 'target', 'value', 'detail') if step.get(k))
                     for step in bundle.get('steps', [])]
    return dict(probe_id=check.probe_id, check_id=check.id, steps=steps,
                expected=check.expected, observed=check.observed, evidence=check.evidence.model_dump())


def aggregate(catalogue, links, checks, confirmations=None, *, llm_unavailable=False, run_dir=None):
    confirmations = confirmations or {}
    by_id = {link.requirement_id: link for link in links.links}
    verdicts = []
    for req in catalogue.requirements:
        link = by_id[req.id]
        linked = [c for c in checks if any(matches(c, family) for family in link.families)]
        confirmed = [c for c in linked if c.status == 'fail' and
                     confirmations.get((c.probe_id, c.id)) == 'confirmed']
        reason, status, failure = None, 'unverified', None
        if llm_unavailable:
            reason = 'llm_unavailable'
        elif req.testability == Testability.NOT_TESTABLE:
            reason = 'not_testable'
        elif confirmed:
            status = 'failed'
            failure = failure_details(confirmed[0], run_dir)
            failure['requirement'] = req.model_dump(mode='json')
            failure['checks'] = [failure_details(c, run_dir) for c in confirmed]
        elif not linked:
            reason = 'no_linked_checks'
        elif any(c.status == 'fail' and confirmations.get((c.probe_id, c.id)) == 'flaky' for c in linked):
            reason = 'flaky'
        elif any(c.status == 'fail' and (c.probe_id, c.id) not in confirmations for c in linked):
            reason = 'unconfirmed'  # failure seen once, never re-run (e.g. --no-confirm)
        elif any(c.status == 'fail' for c in linked):
            reason = 'probe_blocked'  # re-run attempted but could not complete
        elif any(c.status == 'unknown' and 'concurrent' in (c.reason or '').lower() for c in linked):
            reason = 'concurrent_activity'
        elif any(c.status == 'unknown' and any(word in (c.reason or '').lower()
                                              for word in ('blocked', 'errored', 'unavailable', 'probe did not'))
                 for c in linked):
            reason = 'probe_blocked'
        elif any(c.needs_judge and c.status == 'unknown' for c in linked):
            reason = 'needs_judgement'
        elif any(c.status == 'unknown' for c in linked):
            reason = 'checks_unknown'
        elif link.coverage != 'full':
            reason = 'partial_coverage'
        else:
            status = 'passed'
        matrix = {}
        for family in link.families:
            counts = Counter(c.status for c in linked if matches(c, family))
            matrix[family] = {s: counts[s] for s in ('pass', 'fail', 'unknown')}
        verdicts.append(Verdict(
            requirement_id=req.id, area=req.area.value, title=req.title, status=status, reason=reason,
            coverage=link.coverage, failure=failure, matrix=matrix,
            unknown_checks=[c.id for c in linked if c.status == 'unknown'],
            checks=[dict(id=c.id, probe_id=c.probe_id, status=c.status,
                         confirmed=confirmations.get((c.probe_id, c.id), 'unconfirmed')
                         if c.status == 'fail' else None) for c in linked]))
    return verdicts
