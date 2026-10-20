"""Compare stable check identities, retaining missing checks as unknown."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def compare(base: Path, variant: Path) -> dict:
    def load(path):
        return {(c['probe_id'], c['id']): c for c in json.loads((path / 'checks.json').read_text())['checks']}
    before, after = load(base), load(variant)
    changes = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key, {}).get('status', 'unknown'), after.get(key, {}).get('status', 'unknown')
        if a != b:
            changes.append({'probe_id': key[0], 'check_id': key[1], 'before': a, 'after': b,
                            'reason': after.get(key, {}).get('reason', 'check absent')})
    return {'base': str(base), 'variant': str(variant), 'transitions': dict(Counter(
        f"{c['before']}->{c['after']}" for c in changes)), 'flips': [c for c in changes
        if {c['before'], c['after']} == {'pass', 'fail'}], 'changes': changes}
