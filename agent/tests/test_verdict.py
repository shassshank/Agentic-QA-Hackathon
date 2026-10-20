"""Offline verdict tests exercise uncertainty, coverage and cache boundaries."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from qa_agent.cli import app
from qa_agent.llm import CostMeter
from qa_agent.llm.base import LLMResponse, LLMUnavailable
from qa_agent.models import Requirement, RequirementCatalogue, CostEntry
from qa_agent.verify.models import Check, CheckReport
from qa_agent.verdict.aggregate import aggregate
from qa_agent.verdict.confirm import confirm_failures
from qa_agent.verdict.judge import judge_checks
from qa_agent.verdict.link import link_requirements, validate, matches
from qa_agent.verdict.models import AssertionLink, Link, LinkResult, JudgeResult, Judgement
from qa_agent.verdict.run import run_verdict


def catalogue(n=1, **updates):
    requirements = [Requirement(id=f'GEN-{i:03}', area='GEN', title='Clear controls', statement='Controls are clear',
                                source_bullets=[], source_quote='', testability='automated',
                                assertions=[dict(description='Clear', oracle='judge')], scenario_hints=[])
                    for i in range(1, n + 1)]
    for req in requirements:
        for key, value in updates.items():
            setattr(req, key, value)
    return RequirementCatalogue(prd_sha256='sha', model='fake', prompt_version='v3', generated_at=datetime.now(UTC),
                                bullets=[], requirements=requirements, excluded=[])


def check(status='pass', **updates):
    fields = dict(id='general.labels.readable', behaviour='general.labels', area='GEN', title='Controls',
                  status=status, reason=None if status == 'pass' else 'missing evidence', expected='Readable labels',
                  observed='Send', probe_id='gen.labels')
    if updates.get('needs_judge') and status == 'unknown' and 'reason' not in updates:
        fields['reason'] = 'wording quality requires judge'  # what verify.Builder.judge sets
    fields.update(updates)
    return Check(**fields)


def test_judge_never_clears_provenance_unknowns(tmp_path):
    client = FakeClient()
    blocked = check('unknown', needs_judge=True, reason='concurrent activity')
    checked, _ = judge_checks([blocked], client, cache_dir=tmp_path)
    assert (checked[0].status, checked[0].reason) == ('unknown', 'concurrent activity') and not client.calls


def test_judge_quote_must_be_visible_text_not_a_key():
    from qa_agent.verdict.judge import quote_supported

    visible = {'amount_text': '$100', 'type': 'Transfer'}
    assert not quote_supported('"type" shows direction', visible)
    assert not quote_supported('"$1" is enough', {'t': '$1'})  # too short to be evidence
    assert quote_supported('"Transfer" row shows "$100"', visible)


def links(coverage='full', families=None):
    families = families or ['general.labels']
    return LinkResult(links=[Link(requirement_id='GEN-001', families=families, coverage=coverage,
                                 assertions=[AssertionLink(index=0, families=families)])])


def test_full_coverage_claim_without_assertion_mapping_is_downgraded():
    claimed = LinkResult(links=[Link(requirement_id='GEN-001', families=['general.labels'], coverage='full')])
    assert validate(claimed, catalogue(), [check()]).links[0].coverage == 'partial'
    two = catalogue(assertions=[dict(description='a', oracle='judge'), dict(description='b', oracle='ui_text')])
    half = LinkResult(links=[Link(requirement_id='GEN-001', coverage='full', families=['general.labels'],
                                  assertions=[AssertionLink(index=0, families=['general.labels'])])])
    assert validate(half, two, [check()]).links[0].coverage == 'partial'
    assert validate(links(), catalogue(), [check()]).links[0].coverage == 'full'


class FakeClient:
    provider = 'fake'

    def __init__(self, result=None, fail=False, meter=None):
        self.result, self.fail, self.meter = result, fail, meter
        self.calls = []

    def model_for(self, tier):
        return 'fake-' + tier

    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.fail:
            raise LLMUnavailable('offline')
        result = self.result
        if kwargs['phase'] == 'judge' and result is None:
            items = json.loads(prompt.split('\n', 1)[1])
            result = JudgeResult(items=[Judgement(item_id=i['item_id'], verdict='pass', rationale='"Send" is clear.')
                                       for i in items])
        usage = CostEntry(phase=kwargs['phase'], model=self.model_for(kwargs['tier']))
        if self.meter:
            self.meter.record(usage)
        return LLMResponse(text=result.model_dump_json(), parsed=result, model=usage.model, usage=usage)


def test_link_validation():
    result = LinkResult(links=[Link(requirement_id='GEN-001', families=['general.labels', 'invented'], coverage='full'),
                              Link(requirement_id='GEN-001'), Link(requirement_id='missing')])
    fixed = validate(result, catalogue(2), [check()])
    assert len(fixed.links) == 2
    assert fixed.links[0].families == ['general.labels']
    assert fixed.links[0].coverage == 'partial'
    assert fixed.links[1].coverage == 'none'
    assert any('unknown family' in w for w in fixed.warnings)
    assert any('missing requirement' in w for w in fixed.warnings)
    fixed = validate(links(), catalogue(testability='not_testable'), [check()])
    assert fixed.links[0].families == []
    assert matches(check(), 'general.labels.readable')
    assert not matches(check(), 'general.labels.other')


def test_link_cache_outcome_independence(tmp_path):
    client = FakeClient(links())
    cat = catalogue()
    first = link_requirements(cat, [check()], client, cache_dir=tmp_path)
    second = link_requirements(cat, [check('fail', observed='DIFFERENT')], client, cache_dir=tmp_path)
    assert first == second and len(client.calls) == 1
    assert 'DIFFERENT' not in client.calls[0][0]
    cat.requirements[0].statement = 'Changed'
    link_requirements(cat, [check()], client, cache_dir=tmp_path)
    assert len(client.calls) == 2


@pytest.mark.parametrize('budget,calls', [(12000, 1), (1, 3)])
def test_judge_batch_split_cache(tmp_path, budget, calls):
    meter = CostMeter()
    client = FakeClient(meter=meter)
    checks = [check('unknown', id=f'item.{i}', needs_judge=True,
                    judge_input={'text': 'Send', 'context': str(i)}) for i in range(3)]
    first, warnings = judge_checks(checks, client, cache_dir=tmp_path, token_budget=budget)
    assert not warnings and all(c.status == 'pass' for c in first)
    assert len(client.calls) == calls
    second, _ = judge_checks(list(reversed(checks)), client, cache_dir=tmp_path)
    assert all(c.status == 'pass' for c in second) and len(client.calls) == calls
    assert meter.by_phase()['judge']['calls'] == calls
    assert all(c.status == 'unknown' for c in checks)


@pytest.mark.parametrize('verdict,rationale', [('unsure', '"Send" lacks context.'), ('pass', 'No quotation'),
                                            ('fail', '"invented" is unclear.')])
def test_judge_uncertainty_and_invalid_quotes(tmp_path, verdict, rationale):
    client = FakeClient(JudgeResult(items=[Judgement(item_id='0', verdict=verdict, rationale=rationale)]))
    checked, _ = judge_checks([check('unknown', needs_judge=True)], client, cache_dir=tmp_path)
    assert checked[0].status == 'unknown'
    assert checked[0].needs_judge


@pytest.mark.parametrize('answer,status,reason', [
    ('does_not_violate', 'unverified', 'link_disputed'),
    ('violates', 'failed', None),
    ('unsure', 'unverified', 'link_disputed'),
])
def test_failure_review(tmp_path, answer, status, reason):
    from qa_agent.verdict.models import Review, ReviewResult
    from qa_agent.verdict.review import review_failures

    c = check('fail')
    failed = aggregate(catalogue(), links('full'), [c], {(c.probe_id, c.id): 'confirmed'})
    client = FakeClient(result=ReviewResult(items=[Review(requirement_id='GEN-001', verdict=answer, rationale='r')]))
    reviewed, warnings = review_failures(failed, catalogue(), client, cache_dir=tmp_path)
    assert (reviewed[0].status, reviewed[0].reason) == (status, reason) and not warnings
    again, _ = review_failures(failed, catalogue(), FakeClient(fail=True), cache_dir=tmp_path)
    assert again[0].status == status  # cached decision reused, no call needed


def test_failure_review_outage_leaves_unverified(tmp_path):
    from qa_agent.verdict.review import mark_unreviewed, review_failures

    c = check('fail')
    failed = aggregate(catalogue(), links('full'), [c], {(c.probe_id, c.id): 'confirmed'})
    reviewed, warnings = review_failures(failed, catalogue(), FakeClient(fail=True), cache_dir=tmp_path)
    assert (reviewed[0].status, reviewed[0].reason) == ('unverified', 'unreviewed') and warnings
    assert mark_unreviewed(failed)[0].reason == 'unreviewed'  # --no-judge / no client path


def test_empty_link_reply_raises_and_is_not_cached(tmp_path):
    # A reply that misses the schema's wrapper still validates as an empty LinkResult (all defaults).
    client = FakeClient(result=LinkResult())
    with pytest.raises(LLMUnavailable):
        link_requirements(catalogue(), [check()], client, cache_dir=tmp_path)
    assert not list((tmp_path / 'link').glob('*.json')) if (tmp_path / 'link').exists() else True


def test_fail_without_rerun_is_unconfirmed_not_blocked():
    c = check('fail')
    v = aggregate(catalogue(), links('full'), [c], {})[0]
    assert (v.status, v.reason) == ('unverified', 'unconfirmed')


def test_judge_never_overrides_deterministic_fail(tmp_path):
    client = FakeClient()
    checked, _ = judge_checks([check('fail', needs_judge=True)], client, cache_dir=tmp_path)
    assert checked[0].status == 'fail' and not client.calls


@pytest.mark.parametrize('status,coverage,confirmation,updates,expected,reason', [
    ('pass', 'full', None, {}, 'passed', None),
    ('fail', 'full', 'confirmed', {}, 'failed', None),
    ('fail', 'partial', 'confirmed', {}, 'failed', None),
    ('fail', 'full', 'flaky', {}, 'unverified', 'flaky'),
    ('fail', 'full', 'unconfirmed', {}, 'unverified', 'probe_blocked'),
    ('pass', 'partial', None, {}, 'unverified', 'partial_coverage'),
    ('unknown', 'full', None, {}, 'unverified', 'checks_unknown'),
    ('unknown', 'full', None, {'needs_judge': True}, 'unverified', 'needs_judgement'),
    ('unknown', 'full', None, {'reason': 'concurrent activity'}, 'unverified', 'concurrent_activity'),
    ('unknown', 'full', None, {'reason': 'probe blocked'}, 'unverified', 'probe_blocked'),
])
def test_aggregate(status, coverage, confirmation, updates, expected, reason):
    c = check(status, **updates)
    v = aggregate(catalogue(), links(coverage), [c], {(c.probe_id, c.id): confirmation})[0]
    assert (v.status, v.reason) == (expected, reason)
    assert v.matrix['general.labels'][status] == 1
    if expected == 'failed':
        assert v.failure['expected'] == c.expected
        assert v.failure['requirement']['id'] == 'GEN-001'


def test_aggregate_no_link_and_not_testable():
    assert aggregate(catalogue(), links(), [])[0].reason == 'no_linked_checks'
    assert aggregate(catalogue(testability='not_testable'), links(), [check()])[0].reason == 'not_testable'


@pytest.mark.parametrize('probe_status,repeat,expected', [
    ('completed', 'fail', 'confirmed'), ('completed', 'pass', 'flaky'), ('completed', 'unknown', 'flaky'),
    ('blocked', 'fail', 'unconfirmed'), ('errored', 'fail', 'unconfirmed'),
])
def test_confirmation(tmp_path, probe_status, repeat, expected):
    calls = []
    def runner(url, out, *, only):
        calls.append((url, out, only))
        return [dict(id='gen.labels', status=probe_status)]
    def evaluator(obs):
        return SimpleNamespace(checks=[check(repeat)])
    c = check('fail')
    result = confirm_failures([c, check('fail', id='another')], 'http://offline', tmp_path,
                              runner=runner, evaluator=evaluator)
    assert result[(c.probe_id, c.id)] == expected
    assert result[(c.probe_id, 'another')] == 'unconfirmed'
    assert len(calls) == 1 and calls[0][2] == ['gen.labels']


def write_checks(path, checks):
    report = CheckReport(run_tag='offline', base_url='http://offline', generated_at='', checks=checks, summary={})
    (path / 'checks.json').write_text(report.model_dump_json())


def test_unavailable_all_unverified(tmp_path):
    write_checks(tmp_path, [check()])
    result = run_verdict(tmp_path, catalogue(2), FakeClient(fail=True), cache_dir=tmp_path, no_confirm=True)
    assert result.summary == dict(passed=0, failed=0, unverified=2)
    assert all(v.reason == 'llm_unavailable' for v in result.verdicts)
    assert (tmp_path / 'verdicts.json').exists()


def test_warm_pipeline_zero_calls(tmp_path):
    write_checks(tmp_path, [check('unknown', needs_judge=True)])
    class Client(FakeClient):
        def generate(self, prompt, **kwargs):
            self.result = links() if kwargs['phase'] == 'link' else None
            return super().generate(prompt, **kwargs)
    first = Client()
    run_verdict(tmp_path, catalogue(), first, cache_dir=tmp_path, no_confirm=True)
    assert len(first.calls) == 2
    warm = Client(fail=True)
    result = run_verdict(tmp_path, catalogue(), warm, cache_dir=tmp_path, no_confirm=True)
    assert result.summary['passed'] == 1 and warm.calls == []


def test_offline_cli(tmp_path, monkeypatch):
    write_checks(tmp_path, [check('fail')])
    cat = tmp_path / 'requirements.json'
    cat.write_text(catalogue().model_dump_json())
    link = tmp_path / 'links.json'
    link.write_text(links().model_dump_json())
    def forbidden(*args, **kwargs):
        raise AssertionError('Must not initialize LLM')
    monkeypatch.setattr('qa_agent.llm.get_client', forbidden)
    result = CliRunner().invoke(app, ['verdict', '--run', str(tmp_path), '--catalogue', str(cat),
                                      '--link-file', str(link), '--no-judge', '--no-confirm'])
    assert result.exit_code == 0, result.output
    verdicts = json.loads((tmp_path / 'verdicts.json').read_text())
    assert verdicts['summary'] == dict(passed=0, failed=0, unverified=1)
    assert verdicts['cost'] == {}


def test_judge_unavailable_keeps_unknown(tmp_path):
    checked, warnings = judge_checks([check('unknown', needs_judge=True)], FakeClient(fail=True),
                                     cache_dir=tmp_path)
    assert checked[0].status == 'unknown' and warnings


def test_confirmation_exception_is_unconfirmed(tmp_path):
    def runner(*args, **kwargs):
        raise RuntimeError('browser unavailable')
    c = check('fail')
    result = confirm_failures([c], 'http://offline', tmp_path, runner=runner)
    assert result[(c.probe_id, c.id)] == 'unconfirmed'


def test_failure_has_human_steps_and_evidence(tmp_path):
    path = tmp_path / 'evidence' / 'gen.labels'
    path.mkdir(parents=True)
    (path / 'evidence.json').write_text(json.dumps({'steps': [
        {'action': 'goto', 'target': '/transfer'}, {'action': 'fill', 'target': 'Amount', 'value': '0'}]}))
    c = check('fail', evidence={'screenshots': ['failure.png'], 'network': ['evidence.json#/network/0']})
    verdict = aggregate(catalogue(), links(), [c], {(c.probe_id, c.id): 'confirmed'}, run_dir=tmp_path)[0]
    assert verdict.failure['steps'] == ['goto /transfer', 'fill Amount 0']
    assert verdict.failure['evidence']['screenshots'] == ['failure.png']


def test_judge_content_and_prompt_invalidate_cache(tmp_path, monkeypatch):
    from qa_agent.verdict import judge
    client = FakeClient()
    c = check('unknown', needs_judge=True)
    judge_checks([c], client, cache_dir=tmp_path)
    c.expected = 'Different criterion'
    judge_checks([c], client, cache_dir=tmp_path)
    monkeypatch.setattr(judge, 'SYSTEM_PROMPT', judge.SYSTEM_PROMPT + '\nBe precise.')
    judge_checks([c], client, cache_dir=tmp_path)
    assert len(client.calls) == 3
