"""Resilience stays conservative and independent of chaos fixtures."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qa_agent.chaos.compare import compare
from qa_agent.recover.heal import Decision, Healer, match
from qa_agent.recover.policy import RecoveryPolicy
from qa_agent.verify.util import content


def candidate(index, name, role='button'):
    return {'index': index, 'name': name, 'label': '', 'role': role}


@pytest.mark.parametrize(('wanted', 'name', 'role'), [
    ('enter demo', 'Begin the demo', 'button'), ('memo', 'Description', 'textbox'),
    ('from account', 'Origin account', 'combobox'), ('log out', 'Exit', 'button'),
])
def test_match_synonyms(wanted, name, role):
    assert match(wanted, role, [candidate(0, name, role)])


def test_match_rejects_wrong_role_unrelated_and_ties():
    assert match('memo', 'textbox', [candidate(0, 'Note')]) is None
    assert match('delete account', 'button', [candidate(0, 'Enter demo')]) is None
    assert match('memo', None, [candidate(0, 'Note'), candidate(1, 'Description')]) is None


class FakeClient:
    def __init__(self, decision):
        self.calls = 0
        self.decision = decision

    def model_for(self, tier):
        assert tier == 'fast'
        return 'fake-model'

    def generate(self, prompt, **kwargs):
        assert kwargs == {'phase': 'heal', 'tier': 'fast', 'schema': Decision}
        self.calls += 1
        return SimpleNamespace(parsed=self.decision)


def test_disk_cache_warm_and_snapshot_invalidation(tmp_path):
    client = FakeClient({'index': 0, 'confidence': .95, 'reason': 'intent matches'})
    snapshot = [candidate(0, 'Continue')]
    assert Healer(tmp_path, client).choose('click:enter', 'enter', 'button', snapshot)[1] == 'llm'
    assert Healer(tmp_path, client).choose('click:enter', 'enter', 'button', snapshot)
    assert client.calls == 1
    assert Healer(tmp_path, client).choose('click:enter', 'enter', 'button', [candidate(0, 'Proceed')])
    assert client.calls == 2


@pytest.mark.parametrize('decision', [
    {'index': None, 'confidence': 1, 'reason': 'ambiguous'},
    {'index': 99, 'confidence': 1, 'reason': 'invalid'},
    {'index': 0, 'confidence': .5, 'reason': 'uncertain'},
])
def test_llm_abstains_or_invalid_result_blocks(tmp_path, decision):
    assert Healer(tmp_path, FakeClient(decision)).choose('click:enter', 'enter', 'button',
                                                      [candidate(0, 'Continue')]) is None


def test_no_client_never_calls_model(tmp_path):
    assert Healer(tmp_path).choose('click:enter', 'enter', 'button', [candidate(0, 'Continue')]) is None


@pytest.mark.parametrize('probe', ['ses.logout', 'ses.no_session', 'gen.loading', 'gen.recoverable_error'])
def test_policy_opt_out(probe):
    policy = RecoveryPolicy(probe)
    assert not policy.allow('api', 'goto', '/view')
    assert not policy.retry_step('click', 'detached', 0)


def test_policy_never_retries_submit_and_bounds_other_retries():
    policy = RecoveryPolicy('trf.valid')
    for kind in ('api', 'session', 'step'):
        assert not policy.allow(kind, 'submit', 'Send')
    for attempt in range(4):
        assert not policy.retry_step('submit', 'detached', attempt)
    assert policy.allow('api', 'goto', '/view')
    assert not policy.allow('api', 'goto', '/view')
    assert policy.retry_step('fill', 'detached', 0)
    assert policy.retry_step('fill', 'not stable', 1)
    assert not policy.retry_step('fill', 'detached', 2)
    assert not policy.retry_step('click', 'timeout', 0)


def test_chaos_import_isolation():
    root = Path(__file__).parents[1] / 'qa_agent'
    for path in root.rglob('*.py'):
        if 'chaos' in path.relative_to(root).parts or path == root / 'execute/browser.py':
            continue
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert 'chaos.variants' not in (node.module or ''), path
                if (node.module or '').endswith('chaos'):
                    assert all(a.name != 'variants' for a in node.names), path
            if isinstance(node, ast.Import):
                assert all('chaos' not in alias.name for alias in node.names), path
        assert 'RENAMES' not in source and 'variants.py' not in source, path
    for path in (root / 'recover').rglob('*.py'):
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr in {'read_text', 'read_bytes'} and 'variant' in ast.unparse(n)
                       for n in ast.walk(ast.parse(path.read_text())))


def test_content_uses_discovered_paths():
    assert content({'requested': '/overview', 'nav_paths': {'dashboard': '/overview'}, 'account_cards': 2})
    assert not content({'requested': '/overview', 'nav_paths': {'dashboard': '/overview'}, 'account_cards': 0,
                        'transaction_rows': 3})


def test_compare_missing_and_changed_checks(tmp_path):
    base, variant = tmp_path / 'base', tmp_path / 'variant'
    for path, checks in [(base, [{'probe_id': 'a', 'id': 'x', 'status': 'pass'},
                                {'probe_id': 'a', 'id': 'y', 'status': 'fail'}]),
                         (variant, [{'probe_id': 'a', 'id': 'x', 'status': 'fail'}])]:
        path.mkdir()
        (path / 'checks.json').write_text(json.dumps({'checks': checks}))
    result = compare(base, variant)
    assert result['transitions'] == {'pass->fail': 1, 'fail->unknown': 1}
    assert len(result['flips']) == 1


def test_session_reentry_bounded_across_paths():
    policy = RecoveryPolicy('acc.list')
    assert policy.allow('session', 'goto', '/one')
    assert not policy.allow('session', 'goto', '/two')


def test_action_error_submit_is_never_replayed(tmp_path):
    from playwright.sync_api import Error
    from qa_agent.execute.browser import ActionError, ScenarioRun
    from qa_agent.execute.evidence import EvidenceRecorder

    run = ScenarioRun(SimpleNamespace(url='http://example.test'), EvidenceRecorder(tmp_path),
                      'http://example.test', 10)
    calls = []

    def submit():
        calls.append('submit')
        raise Error('element detached')

    with pytest.raises(ActionError):
        run._act('submit', 'Send', submit)
    assert calls == ['submit']


def test_step_retries_back_off_and_stop(tmp_path):
    from playwright.sync_api import Error
    from qa_agent.execute.browser import ActionError, ScenarioRun
    from qa_agent.execute.evidence import EvidenceRecorder

    delays = []
    from qa_agent.recover.policy import RecoveryPolicy

    run = ScenarioRun(SimpleNamespace(url='http://example.test', wait_for_timeout=delays.append),
                      EvidenceRecorder(tmp_path), 'http://example.test', 10)
    calls = []

    def fill():
        calls.append('fill')
        raise Error('element detached')

    with pytest.raises(ActionError):
        run._act('fill', 'Note', fill)
    assert calls == ['fill']  # ad-hoc runs (no probe) never recover
    calls.clear()
    run.policy = RecoveryPolicy('trf.valid')
    with pytest.raises(ActionError):
        run._act('fill', 'Note', fill)
    assert len(calls) == 3 and delays == [100, 200]
    assert len([step for step in run.recorder.steps if step.action == 'recovery']) == 2
