"""Run verdict phases with model outages represented as uncertainty."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from ..llm import CostMeter
from ..models import RequirementCatalogue
from ..verify.models import CheckReport
from .aggregate import aggregate
from .confirm import confirm_failures
from .judge import judge_checks, judge_checks_jev
from .link import link_requirements, link_requirements_jev, validate
from .review import mark_unreviewed, review_failures, review_failures_jev
from .models import LinkResult, VerdictReport


def scenario_titles(run_dir: Path) -> dict[str, str]:
    """Probe id → scenario title from the run's observations; empty when the file is absent."""
    try:
        results = json.loads((run_dir / 'observations.json').read_text()).get('results', [])
        return {r['id']: r['title'] for r in results if r.get('id') and r.get('title')}
    except (OSError, ValueError, AttributeError):
        return {}


def run_verdict(run_dir: Path, catalogue: RequirementCatalogue, client=None, *, cache_dir: Path, decider=None,
                meter: CostMeter | None = None, link_result: LinkResult | None = None,
                no_judge=False, no_confirm=False, runner=None) -> VerdictReport:
    meter = meter or CostMeter()
    report = CheckReport.model_validate_json((run_dir / 'checks.json').read_text())
    checks = report.checks
    warnings = []
    unavailable = False
    try:
        links = (validate(link_result, catalogue, checks) if link_result is not None else
                 link_requirements_jev(catalogue, checks, decider, client, cache_dir=cache_dir,
                                       scenarios=scenario_titles(run_dir)) if decider else
                 link_requirements(catalogue, checks, client, cache_dir=cache_dir))
    except Exception as exc:
        unavailable = True
        warnings.append(f'Link unavailable: {type(exc).__name__}: {exc}')
        links = validate(LinkResult(), catalogue, checks)
    warnings.extend(links.warnings)
    if not unavailable and not no_judge:
        try:
            checks, judge_warnings = (judge_checks_jev(checks, decider, client, cache_dir=cache_dir) if decider else
                                      judge_checks(checks, client, cache_dir=cache_dir))
            warnings.extend(judge_warnings)
        except Exception as exc:
            warnings.append(f'Judge unavailable: {type(exc).__name__}: {exc}')
    confirmations = {}
    if not unavailable and not no_confirm:
        confirmations = confirm_failures(checks, report.base_url, run_dir, runner=runner)
    verdicts = aggregate(catalogue, links, checks, confirmations, llm_unavailable=unavailable, run_dir=run_dir)
    if not unavailable and not no_judge and (client is not None or decider is not None):
        verdicts, review_warnings = (review_failures_jev(verdicts, catalogue, decider, client, cache_dir=cache_dir)
                                    if decider else review_failures(verdicts, catalogue, client, cache_dir=cache_dir))
        warnings.extend(review_warnings)
    else:
        verdicts = mark_unreviewed(verdicts)
    counts = Counter(v.status for v in verdicts)
    result = VerdictReport(run_tag=report.run_tag, catalogue={
        'model': catalogue.model, 'prompt_version': catalogue.prompt_version, 'prd_sha256': catalogue.prd_sha256},
        verdicts=verdicts, summary={s: counts[s] for s in ('passed', 'failed', 'unverified')},
        cost=meter.by_phase(), warnings=warnings)
    (run_dir / 'verdicts.json').write_text(result.model_dump_json(indent=2))
    return result
