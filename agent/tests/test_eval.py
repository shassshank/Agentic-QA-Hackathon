"""Offline patch and scoring tests."""
import ast
import shutil

import pytest

from eval.harness import ROOT, TARGET, apply_patches, definitions, resolve_variant
from eval.score import aggregate, model_diff, score_run


def report(**statuses):
    return {'verdicts': [dict(requirement_id=k, status=v, coverage='full',
                             review={'probabilities': {'violates': .9}}) for k, v in statuses.items()]}


def test_patch_count(tmp_path):
    (tmp_path / 'x').write_text('a a')
    p = dict(file='x', find='a', replace='b', count=2)
    assert apply_patches(tmp_path, [p])[0]['matches'] == 2
    with pytest.raises(ValueError, match='count mismatch'):
        apply_patches(tmp_path, [p])
    with pytest.raises(ValueError, match='outside'):
        apply_patches(tmp_path, [{**p, 'file': '../x'}])


def test_base_order(tmp_path):
    data = {'variants': [dict(name='a', patches=[dict(file='x', find='a', replace='b', count=1)],
                              must_fail=['A'], may_fail=['B']),
                         dict(name='b', base='a', patches=[dict(file='x', find='b', replace='c', count=1)],
                              must_fail=['C'])]}
    v = resolve_variant(data, 'b')
    assert v['must_fail'] == ['A', 'C'] and v['may_fail'] == ['B']
    assert v['own_must_fail'] == ['C']
    (tmp_path / 'x').write_text('a')
    apply_patches(tmp_path, v['patches'])
    assert (tmp_path / 'x').read_text() == 'c'
    data['variants'][0]['base'] = 'b'
    with pytest.raises(ValueError, match='cycle'):
        resolve_variant(data, 'b')


@pytest.mark.parametrize('name', [v['name'] for v in definitions()['variants']])
def test_every_variant_patch_count(name, tmp_path):
    for part in ('backend', 'frontend/src'):
        shutil.copytree(TARGET / part, tmp_path / part,
                        ignore=shutil.ignore_patterns('node_modules', '.venv', '__pycache__', '.env'))
    applied = apply_patches(tmp_path, resolve_variant(definitions(), name)['patches'])
    assert all(p['matches'] == p['count'] for p in applied)
    assert any('allow_origins=' in p['find'] for p in applied)
    for path in (tmp_path / 'backend').rglob('*.py'):
        ast.parse(path.read_text())


def test_scoring():
    v = dict(must_fail=['A', 'B', 'C'], may_fail=['D'], own_must_fail=['A'])
    r = score_run(v, report(A='failed', B='passed', C='unverified', D='failed', E='failed'),
                  {'total_cost_usd': 1.2}, {'results': [{'duration_ms': 123}]})
    assert [r[k] for k in ('TP', 'FP', 'FN_pass', 'FN_unverified')] == [1, 1, 1, 1]
    assert r['precision'] == .5 and r['recall'] == 1 / 3
    assert r['counts'] == dict(passed=1, failed=3, unverified=1)
    assert r['coverage'] == {'full': 5}
    assert r['review_probabilities']['A'] == {'violates': .9}
    assert r['cost_total'] == 1.2 and r['probe_runtime_ms'] == 123
    assert len(r['rows']) == 5 and r['detected']


def test_empty_and_fixed():
    assert score_run(dict(now_correct=['A']), report(A='failed'))['FP'] == 1
    r = score_run({}, report(A='passed'))
    assert r['precision'] is None and r['recall'] is None
    assert aggregate([]) == dict(precision=None, recall=None, mutant_detection_rate=None)
    assert score_run(dict(must_fail=['A']), report())['missing'] == ['A']


def test_micro_own_detection():
    v = dict(must_fail=['A', 'B'], own_must_fail=['B'])
    a = dict(variant='m-one', **score_run(v, report(A='failed', B='passed')))
    b = dict(variant='m-two', **score_run(v, report(A='failed', B='failed', C='failed')))
    assert not a['detected'] and b['detected']
    assert aggregate([a, b]) == dict(precision=.75, recall=.75, mutant_detection_rate=.5)


def test_model_diff():
    assert model_diff({'a': report(A='failed', B='passed'), 'b': report(A='passed', B='passed')}) == {
        'A': {'a': 'failed', 'b': 'passed'}}
    assert model_diff({}) == {}


def test_agent_never_imports_eval():
    for path in (ROOT / 'qa_agent').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(a.name.split('.')[0] != 'eval' for a in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or '').split('.')[0] != 'eval', path


def test_detection_rate_counts_distinct_mutants():
    run = lambda variant, detected: dict(variant=variant, TP=0, FP=0, expected=1, detected=detected)
    # Three detecting reports of one mutant and one missing report of another: one of two mutants detected.
    results = [run('m-a', True)] * 3 + [run('m-b', False)]
    assert aggregate(results)['mutant_detection_rate'] == .5
