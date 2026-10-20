"""Repeat each failing probe once before treating failures as reproducible."""
from __future__ import annotations

from pathlib import Path
from ..verify.run import evaluate


def confirm_failures(checks, base_url: str, run_dir: Path, *, runner=None, evaluator=evaluate):
    confirmations = {}
    if runner is None:
        from ..plan.registry import run_probes
        runner = run_probes
    for probe_id in dict.fromkeys(c.probe_id for c in checks if c.status == 'fail'):
        failing = [c for c in checks if c.probe_id == probe_id and c.status == 'fail']
        for check in failing:
            confirmations[(probe_id, check.id)] = 'unconfirmed'
        try:
            out = run_dir / 'confirm' / probe_id
            out.mkdir(parents=True, exist_ok=True)
            results = runner(base_url, out, only=[probe_id])
            rows = [r.model_dump(mode='json') if hasattr(r, 'model_dump') else r for r in results]
            exact = [r for r in rows if r['id'] == probe_id]
            if len(exact) != 1 or exact[0]['status'] != 'completed':
                continue
            report = evaluator(dict(base_url=base_url, results=exact))
            for check in failing:
                repeats = [c for c in report.checks if c.id == check.id and c.probe_id == probe_id]
                if len(repeats) == 1:
                    confirmations[(probe_id, check.id)] = 'confirmed' if repeats[0].status == 'fail' else 'flaky'
        except Exception:
            continue
    return confirmations
