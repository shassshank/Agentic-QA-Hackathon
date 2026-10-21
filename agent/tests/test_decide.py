"""Offline decision-layer boundaries and escalation."""
import json
from types import SimpleNamespace

import httpx
import pytest

from qa_agent.llm import CostMeter, get_decider
from qa_agent.config import Settings
from qa_agent.models import Assertion
from qa_agent.llm.typesafe import DecisionClient, DecisionUnavailable
from qa_agent.verdict.judge import judge_checks_jev
from qa_agent.verdict.review import review_failures_jev
from qa_agent.verdict.link import link_requirements_jev
from qa_agent.verdict.models import JudgeResult, Judgement, ReviewResult, Review
from qa_agent.verdict.aggregate import aggregate
from qa_agent.recover.heal import Healer
from test_verdict import catalogue, check, links, FakeClient, write_checks
from qa_agent.verdict.run import run_verdict


def noul(p):
    return {'type': 'noul', 'noul': p}


def choice(name, p, **others):
    return dict(type='choice', choice=name, confidence=p, probabilities={name: p, **others})


class Decider:
    model = 'jev-1.13.0'

    def __init__(self, answers, *later):
        self.stages, self.calls = [answers, *later], []

    def ask_many(self, requests, *, phase):
        self.calls.append((requests, phase))
        return self.stages[min(len(self.calls), len(self.stages)) - 1]


def client(tmp_path, handler, **kwargs):
    meter = CostMeter()
    c = DecisionClient('private-key', meter=meter, transport=httpx.MockTransport(handler), **kwargs)
    c.cache_dir = tmp_path
    return c, meter


def response(answers):
    return httpx.Response(200, json=dict(model='jev-1.13.0', answers=answers,
                                        usage=dict(input_tokens=100, output_tokens=2)))


def test_client_request_cache_meter(tmp_path):
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer private-key'
        assert json.loads(request.content) == dict(model='jev-1.13.0', state={'x': 1},
                                                   questions={'q': {'type': 'noul'}})
        return response({'q': noul(.9)})
    c, meter = client(tmp_path, handler)
    assert 'private-key' not in repr(c)
    for _ in range(2):
        assert c.ask({'x': 1}, {'q': {'type': 'noul'}}, phase='judge')['q']['noul'] == .9
    assert meter.entries[0].input_tokens == 100
    assert meter.entries[1].cache_hit and meter.entries[1].input_tokens == 0
    assert meter.total_cost() == pytest.approx(.0000042)
    assert get_decider(Settings(), meter) is None


def test_retries(tmp_path, monkeypatch):
    delays, codes = [], iter([429, 529, 200])
    monkeypatch.setattr('qa_agent.llm.typesafe.time.sleep', delays.append)
    def handler(request):
        code = next(codes)
        return response({'q': noul(.5)}) if code == 200 else httpx.Response(code, headers={'Retry-After': '20'})
    c, _ = client(tmp_path, handler)
    c.ask({}, {'q': {'type': 'noul'}}, phase='judge')
    assert delays == [10, 10]


@pytest.mark.parametrize('bad', [noul(2), {'type': 'choice'}, noul(True)])
def test_malformed(tmp_path, bad):
    c, _ = client(tmp_path, lambda r: response({'q': bad}))
    with pytest.raises(DecisionUnavailable):
        c.ask({}, {'q': {'type': 'noul'}}, phase='judge')
    assert not list(tmp_path.rglob('*.json'))


def test_permanent_and_ordered_failures(tmp_path):
    c, _ = client(tmp_path, lambda r: httpx.Response(422, json={'detail': ['private-key']}))
    results = c.ask_many([({}, {}), ({'second': True}, {})], phase='link')
    assert len(results) == 2 and all(isinstance(r, DecisionUnavailable) for r in results)
    assert '422' in str(results[0]) and 'private-key' not in str(results[0])


@pytest.mark.parametrize('p,status', [(.85, 'pass'), (.15, 'fail'), (.62, 'unknown')])
def test_judge_thresholds(tmp_path, p, status):
    result, _ = judge_checks_jev([check('unknown', needs_judge=True)], Decider([{'meets': noul(p)}]),
                                 None, cache_dir=tmp_path)
    assert result[0].status == status
    if status == 'unknown':
        assert 'no escalation' in result[0].reason


def test_judge_escalation_and_provenance(tmp_path):
    gemini = FakeClient(JudgeResult(items=[Judgement(item_id='0', verdict='pass', rationale='"Send" is clear')]))
    result, _ = judge_checks_jev([check('unknown', needs_judge=True),
                                 check('unknown', needs_judge=True, reason='probe blocked')],
                                Decider([{'meets': noul(.5)}]), gemini, cache_dir=tmp_path)
    assert [c.status for c in result] == ['pass', 'unknown']
    assert gemini.calls[0][1]['phase'] == 'judge_escalation'


def test_direction_contradiction(tmp_path):
    decider = Decider([{'direction_shown': noul(1)}])
    c = check('unknown', needs_judge=True, judge_input=dict(criterion='Amount direction',
              visible_text={'amount_text': '−$950.00', 'type': 'deposit'}))
    result, _ = judge_checks_jev([c], decider, None, cache_dir=tmp_path)
    assert result[0].status == 'fail'
    assert decider.calls[0][0] == []


def failed():
    c = check('fail')
    return aggregate(catalogue(), links(), [c], {(c.probe_id, c.id): 'confirmed'})


@pytest.mark.parametrize('answer,status,reason', [
    (choice('violates', .9), 'failed', None),
    (choice('different_behaviour', .9), 'unverified', 'link_disputed'),
    (choice('condition_not_met', .5, different_behaviour=.4, violates=.1), 'unverified', 'link_disputed'),
    (choice('violates', .86, condition_not_met=.14), 'unverified', 'unreviewed'),
    (choice('violates', .8), 'unverified', 'unreviewed'),
    (DecisionUnavailable('outage'), 'unverified', 'unreviewed')])
def test_review(tmp_path, answer, status, reason):
    result, _ = review_failures_jev(failed(), catalogue(), Decider([
        answer if isinstance(answer, Exception) else {'linkage': answer}]), None, cache_dir=tmp_path)
    assert (result[0].status, result[0].reason) == (status, reason)


def test_review_escalation(tmp_path):
    gemini = FakeClient(ReviewResult(items=[Review(requirement_id='GEN-001', verdict='violates', rationale='r')]))
    result, _ = review_failures_jev(failed(), catalogue(), Decider([{'linkage': choice('unsure', .5)}]),
                                   gemini, cache_dir=tmp_path)
    assert result[0].status == 'failed' and result[0].review['escalated']


def test_link_groups(tmp_path):
    from qa_agent.verdict.link import check_groups
    checks = [check(id='general.labels.0'), check(id='general.labels.submit', expected='submit labelled'),
              check(behaviour='other', id='other.x.value')]
    assert check_groups(checks, 'general.labels') == {'general.labels': ['row: Readable labels'],
                                                      'general.labels.submit': ['submit: submit labelled']}
    assert list(check_groups(checks, 'other')) == ['other.value']


def test_link_multiple_and_none(tmp_path):
    checks = [check(), check(behaviour='other', id='other.value')]
    cat = catalogue(2)
    # Stage 1 ranks families; stage 2 verifies each group of the top families with a noul.
    decider = Decider([{'a0': choice('general.labels', .45, other=.3, none=.25)}, {'a0': choice('none', .8)}],
                      [{'tests': noul(.9), 'gap': noul(0)}, {'tests': noul(.85), 'gap': noul(0)}])
    result = link_requirements_jev(cat, checks, decider, None, cache_dir=tmp_path)
    assert result.links[0].families == ['general.labels', 'other']
    assert result.links[0].coverage == 'full'
    assert result.links[1].coverage == 'none'
    assert len(decider.calls[1][0]) == 2  # two candidates; the none-only requirement needs no verification


def test_link_judged_group_candidate(tmp_path):
    checks = [check(), check('unknown', needs_judge=True, id='general.labels.wording')]
    decider = Decider([{'a0': choice('general.labels', .9)}], [{'tests': noul(.4), 'gap': noul(0)}, {'tests': noul(.95), 'gap': noul(0)}])
    result = link_requirements_jev(catalogue(), checks, decider, None, cache_dir=tmp_path)
    # family, then its judged wording group; the family's checks are state, not question text
    assert [s['item']['family']['name'] for s, _ in decider.calls[1][0]] == ['general.labels', 'general.labels.wording']
    assert result.links[0].families == ['general.labels.wording']


def test_link_rejected_groups(tmp_path):
    checks = [check(), check(behaviour='other', id='other.value')]
    decider = Decider([{'a0': choice('general.labels', .9, other=.1)}], [{'tests': noul(.3), 'gap': noul(0)}, {'tests': noul(.79), 'gap': noul(0)}])
    result = link_requirements_jev(catalogue(), checks, decider, None, cache_dir=tmp_path)
    assert result.links[0].coverage == 'none'  # confident ranking alone never links


def test_link_subset_merge(tmp_path):
    gemini = FakeClient(links())
    decider = Decider([{'a0': choice('general.labels', .3)}, {'a0': choice('none', .9)}], [{'tests': noul(.5), 'gap': noul(0)}])
    result = link_requirements_jev(catalogue(2), [check()], decider, gemini, cache_dir=tmp_path)
    assert result.links[0].coverage == 'full' and result.links[1].coverage == 'none'
    assert 'GEN-002' not in gemini.calls[0][0]


def test_link_many_options(tmp_path):
    checks = [check(behaviour=f'f{i}', id=f'f{i}.value') for i in range(254)]
    decider, gemini = Decider([]), FakeClient(links(families=['f0']))
    result = link_requirements_jev(catalogue(), checks, decider, gemini, cache_dir=tmp_path)
    assert result.links[0].families == ['f0'] and not decider.calls


def test_link_partial_and_not_testable(tmp_path):
    cat = catalogue(assertions=[Assertion(description='a', oracle='judge'), Assertion(description='b', oracle='judge')])
    result = link_requirements_jev(cat, [check()], Decider([{'a0': choice('general.labels', .5),
                                                          'a1': choice('general.labels', .2)}],
                                                        [{'tests': noul(.9), 'gap': noul(0)}, {'tests': noul(.2), 'gap': noul(0)}]),
                                   None, cache_dir=tmp_path)
    assert result.links[0].coverage == 'partial'
    decider = Decider([])
    result = link_requirements_jev(catalogue(testability='not_testable'), [check()], decider, None, cache_dir=tmp_path)
    assert result.links[0].coverage == 'none' and decider.calls[0][0] == []


@pytest.mark.parametrize('option,p,accepted', [('c0', .85, True), ('none', 1, False), ('c0', .84, False)])
def test_heal(tmp_path, option, p, accepted):
    healer = Healer(tmp_path, decider=Decider([{'control': choice(option, p)}]))
    result = healer.choose('click:enter', 'enter', 'button', [{'index': 0, 'name': 'Continue', 'role': 'button'}])
    assert bool(result) == accepted
    if accepted:
        assert result[1] == 'jev'


def test_jev_pipeline_and_outage(tmp_path):
    write_checks(tmp_path, [check()])
    report = run_verdict(tmp_path, catalogue(), decider=Decider([{'a0': choice('general.labels', .8)}],
                                                                [{'tests': noul(.9), 'gap': noul(0)}]),
                         cache_dir=tmp_path, no_confirm=True)
    assert report.summary['passed'] == 1
    report = run_verdict(tmp_path, catalogue(), decider=Decider([DecisionUnavailable('offline')]),
                         cache_dir=tmp_path, no_confirm=True)
    assert report.verdicts[0].reason == 'llm_unavailable'


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_exhaustion(tmp_path, monkeypatch, error):
    calls, delays = [], []
    monkeypatch.setattr('qa_agent.llm.typesafe.time.sleep', delays.append)
    def handler(request):
        calls.append(request)
        raise error('private-key', request=request)
    c, meter = client(tmp_path, handler)
    with pytest.raises(DecisionUnavailable) as exc:
        c.ask({}, {}, phase='judge')
    assert 'private-key' not in str(exc.value)
    assert len(calls) == 4 and delays == [.5, 1, 2] and not meter.entries


@pytest.mark.parametrize('answer', [choice('invalid', .9), choice('a', 1.1),
                                     dict(type='choice', choice='a', confidence=.9, probabilities={'a': -1})])
def test_choice_validation(tmp_path, answer):
    c, _ = client(tmp_path, lambda r: response({'q': answer}))
    with pytest.raises(DecisionUnavailable):
        c.ask({}, {'q': dict(type='choice', criteria={'a': None})}, phase='heal')


def test_judge_outage(tmp_path):
    result, warnings = judge_checks_jev([check('unknown', needs_judge=True)],
                                        Decider([DecisionUnavailable('offline')]), None, cache_dir=tmp_path)
    assert result[0].status == 'unknown' and warnings


def test_run_cli_cached_interpretation_without_gemini(tmp_path, monkeypatch):
    from qa_agent import cli
    from qa_agent.interpret.extract import interpret, _sha256, PROMPT_VERSION, SYSTEM_PROMPT
    from qa_agent.models import ExtractionResult
    from typer.testing import CliRunner
    import qa_agent.config
    import qa_agent.llm
    import qa_agent.verdict

    settings = Settings()
    prd = tmp_path / 'prd.md'
    prd.write_text('# Requirements\n- Controls must be clear.\n')
    key = _sha256(prd.read_text(), settings.gemini_model_planner, PROMPT_VERSION, settings.provider,
                  SYSTEM_PROMPT, json.dumps(ExtractionResult.model_json_schema(), sort_keys=True))
    cached = tmp_path / '.cache' / 'interpret' / f'{key}.json'
    cached.parent.mkdir(parents=True)
    cached.write_text(catalogue().model_dump_json())
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(qa_agent.config, 'AGENT_ROOT', tmp_path)
    def unavailable(*args):
        raise RuntimeError('missing Gemini key')
    monkeypatch.setattr(qa_agent.llm, 'get_client', unavailable)
    decider = Decider([])
    monkeypatch.setattr(qa_agent.llm, 'get_decider', lambda *a: decider)
    monkeypatch.setattr(cli, 'probe_command', lambda **kw: None)
    monkeypatch.setattr(cli, 'verify_command', lambda **kw: None)
    calls = []
    def verdict(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(verdicts=[], summary={})
    monkeypatch.setattr(qa_agent.verdict, 'run_verdict', verdict)
    result = CliRunner().invoke(cli.app, ['run', '--prd', str(prd), '--url', 'http://offline',
                                          '--out', str(tmp_path / 'run')])
    assert result.exit_code == 0, result.output
    assert calls[0][0][2] is None and calls[0][1]['decider'] is decider
    assert result.output.count('LLM unavailable:') == 1
    assert '--decider' not in CliRunner().invoke(cli.app, ['run', '--help']).output


def test_explanation_question_and_evidence(tmp_path):
    decider = Decider([{'explains': noul(.93)}])
    c = check('unknown', needs_judge=True, judge_input={'criterion': 'Clear explanation of rejection',
                                                       'visible_text': 'A value is required'})
    result, _ = judge_checks_jev([c], decider, None, cache_dir=tmp_path)
    assert result[0].status == 'pass'
    assert result[0].judge_input['decision']['question'] == 'explains'
    state, questions = decider.calls[0][0][0]
    assert state['item']['visible_text'] == 'A value is required'
    assert '`item.visible_text`' in questions['explains']['instructions']


def test_cli_none_disables_decider(tmp_path, monkeypatch):
    from qa_agent import cli
    from typer.testing import CliRunner
    from test_verdict import links
    write_checks(tmp_path, [check()])
    cat, link = tmp_path / 'requirements.json', tmp_path / 'links.json'
    cat.write_text(catalogue().model_dump_json())
    link.write_text(links().model_dump_json())
    def forbidden(*args):
        raise AssertionError('decider must remain disabled')
    monkeypatch.setattr('qa_agent.llm.get_decider', forbidden)
    monkeypatch.setattr(cli, 'load_settings', lambda: Settings())
    result = CliRunner().invoke(cli.app, ['verdict', '--run', str(tmp_path), '--catalogue', str(cat),
                                          '--link-file', str(link), '--no-judge', '--no-confirm', '--decider', 'none'])
    assert result.exit_code == 0, result.output


def test_link_related_families_and_confident_gap(tmp_path):
    checks = [check(), check(behaviour='other', id='other.value')]
    # A primary family (.9) brings in a related one (.6); a lone .6 would not link.
    decider = Decider([{'a0': choice('general.labels', .7, other=.3)}],
                      [{'tests': noul(.9), 'gap': noul(.2)}, {'tests': noul(.6), 'gap': noul(.1)}])
    result = link_requirements_jev(catalogue(), checks, decider, None, cache_dir=tmp_path)
    assert result.links[0].families == ['general.labels', 'other'] and result.links[0].coverage == 'full'
    # Every primary confidently leaves part of the assertion unverified -> partial, never full.
    decider = Decider([{'a0': choice('general.labels', .7, other=.3)}],
                      [{'tests': noul(.9), 'gap': noul(.95)}, {'tests': noul(.2), 'gap': noul(0)}])
    assert link_requirements_jev(catalogue(), checks, decider, None, cache_dir=tmp_path).links[0].coverage == 'partial'


def test_inconsistent_choice_distribution_rejected(tmp_path):
    bad = dict(type='choice', choice='unsure', confidence=0, probabilities={'violates': .95, 'unsure': .95})
    c, _ = client(tmp_path, lambda r: response({'q': bad}))
    with pytest.raises(DecisionUnavailable):
        c.ask({}, {'q': {'type': 'choice', 'criteria': {'violates': None, 'unsure': None}}}, phase='review')
    assert not list(tmp_path.rglob('*.json'))


def test_protocol_error_and_per_call_cache(tmp_path, monkeypatch):
    monkeypatch.setattr('qa_agent.llm.typesafe.time.sleep', lambda s: None)
    def disconnect(request):
        raise httpx.RemoteProtocolError('Server disconnected without sending a response')
    c, _ = client(tmp_path, disconnect)
    assert isinstance(c.ask_many([({}, {'q': {'type': 'noul'}})], phase='judge')[0], DecisionUnavailable)
    ok, _ = client(tmp_path, lambda r: response({'q': noul(.9)}))
    from qa_agent.llm.typesafe import ask_many
    ask_many(ok, [({'x': 1}, {'q': {'type': 'noul'}})], phase='judge', cache_dir=tmp_path / 'run-a')
    assert list((tmp_path / 'run-a').rglob('*.json')) and ok.cache_dir == tmp_path  # client left unchanged
