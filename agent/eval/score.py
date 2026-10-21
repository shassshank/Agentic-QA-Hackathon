"""Pure evaluation metrics; ground truth stays outside the agent."""
from collections import Counter


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def score_run(variant, report, cost=None, observations=None):
    rows = {r['requirement_id']: r for r in report['verdicts']}
    expected = set(variant.get('must_fail', []))
    allowed = expected | set(variant.get('may_fail', []))
    groups = {s: {rid for rid, row in rows.items() if row['status'] == s}
              for s in ('passed', 'failed', 'unverified')}
    failed = groups['failed']
    tp, fp = len(failed & expected), len(failed - allowed)
    missing = expected - rows.keys()
    return dict(TP=tp, FP=fp, FN_pass=len(groups['passed'] & expected),
                FN_unverified=len(groups['unverified'] & expected), missing=sorted(missing),
                expected=len(expected), precision=ratio(tp, tp + fp), recall=ratio(tp, len(expected)),
                counts={s: len(ids) for s, ids in groups.items()},
                coverage=dict(Counter(r.get('coverage', 'none') for r in rows.values())),
                review_probabilities={rid: (rows[rid].get('review') or {}).get('probabilities')
                                      for rid in sorted(failed)},
                cost_total=(cost or {}).get('total_cost_usd'),
                probe_runtime_ms=sum(r.get('duration_ms', 0) for r in (observations or {}).get('results', [])),
                detected=bool(failed & set(variant.get('own_must_fail', []))),
                rows=[dict(requirement_id=rid, expected=rid in expected, allowed=rid in allowed,
                           status=rows.get(rid, {}).get('status', 'missing'))
                      for rid in sorted(expected | failed)])


def aggregate(results):
    tp = sum(r['TP'] for r in results)
    fp = sum(r['FP'] for r in results)
    # Count distinct mutants, not reports: several model runs of one mutant must not outweigh another mutant.
    # A mutant counts as detected only if every one of its reports detected it.
    by_mutant: dict[str, list[bool]] = {}
    for r in results:
        if r['variant'].startswith('m-'):
            by_mutant.setdefault(r['variant'], []).append(r['detected'])
    return dict(precision=ratio(tp, tp + fp), recall=ratio(tp, sum(r['expected'] for r in results)),
                mutant_detection_rate=ratio(sum(all(d) for d in by_mutant.values()), len(by_mutant)))


def model_diff(reports):
    statuses = {model: {r['requirement_id']: r['status'] for r in report['verdicts']}
                for model, report in reports.items()}
    ids = set().union(*(set(rows) for rows in statuses.values()))
    return {rid: {model: rows.get(rid, 'missing') for model, rows in statuses.items()}
            for rid in sorted(ids) if len({rows.get(rid, 'missing') for rows in statuses.values()}) > 1}
