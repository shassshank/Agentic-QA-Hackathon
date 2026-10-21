"""Repeat each failing probe once before treating failures as reproducible."""
from __future__ import annotations

from pathlib import Path
from ..verify.run import evaluate


def same_judge_input(original, repeat) -> bool:
    """The original was a judged check (it carries judge input) and the rerun asks the judge the same question.

    Judging clears ``needs_judge`` and adds the decision to ``judge_input``, so compare without it.
    """
    if original.judge_input is None or repeat.judge_input is None or original.observed != repeat.observed:
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != 'decision'}
    return strip(original.judge_input) == strip(repeat.judge_input)


def confirm_failures(checks, base_url: str, run_dir: Path, *, runner=None, evaluator=evaluate):
    confirmations = {}
    if runner is None:
        from ..plan.registry import run_probes
        runner = run_probes
    for probe_id in dict.fromkeys(c.probe_id for c in checks if c.status == 'fail'):
        failing = [c for c in checks if c.probe_id == probe_id and c.status == 'fail']
        for check in failing:
            confirmations[(probe_id, check.id)] = 'blocked'  # until a completed re-run says otherwise
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
                confirmations[(probe_id, check.id)] = 'unconfirmed'  # re-run completed; reproduced only below
                repeats = [c for c in report.checks if c.id == check.id and c.probe_id == probe_id]
                if len(repeats) != 1:
                    continue
                repeat = repeats[0]
                if repeat.status == 'fail':
                    confirmations[(probe_id, check.id)] = 'confirmed'
                elif repeat.status == 'pass':
                    confirmations[(probe_id, check.id)] = 'flaky'
                elif repeat.needs_judge and same_judge_input(check, repeat):
                    # A judged failure re-evaluates to 'unknown' (the oracle defers to the judge). The same judge input
                    # recurring reproduces it: the cached judgement of that exact input is what failed. Any other
                    # unknown (e.g. concurrent activity, missing data) has no judge input and stays unconfirmed.
                    confirmations[(probe_id, check.id)] = 'confirmed'
        except Exception:
            continue
    return confirmations
