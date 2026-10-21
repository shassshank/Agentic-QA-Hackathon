import json
import re

import pytest

from qa_agent.report import PLACEHOLDER, render_html, render_markdown, write_report
from qa_agent.report.data import build
from qa_agent.report.steps import describe, describe_recorded, humanize_pattern
from test_verdict import catalogue


@pytest.mark.parametrize('pattern,name', [
    (r'^\s*(from|source)\b', 'From'),
    (r'^\s*(transaction )?type', 'Type'),
    (r'^\s*View\ all\ transactions\s*$', 'View all transactions'),
    ('try again|retry|reload', 'Try again'),
    ('memo|note|reference', 'Memo')])
def test_humanize_pattern(pattern, name):
    assert humanize_pattern(pattern) == name


@pytest.mark.parametrize('action,target,value,text', [
    ('goto', '/transfer', None, 'Open /transfer'),
    ('click', r'link:^\s*Dashboard\s*$', None, 'Click the "Dashboard" link'),
    ('fill', 'amount', '2.00', 'Type "2.00" into the Amount field'),
    ('fill', 'amount', '   ', 'Leave the Amount field as "   "'),
    ('select', r'^\s*(to|destination)\b', 'Rainy Day Savings ', 'Choose "Rainy Day Savings" in the To field'),
    ('submit', 'button:transfer|submit|send|confirm', 'clicks=3', 'Click the "Transfer" button 3 times rapidly'),
    ('throttle', None, '1500ms', 'Slow every network request by 1500 ms'),
    ('throttle', None, '0ms', 'Restore normal network speed'),
    ('reload', None, None, 'Reload the page')])
def test_describe(action, target, value, text):
    assert describe(action, target, value) == text


@pytest.mark.parametrize('step,text', [
    ('click button:enter demo', 'Click the "Enter demo" button'),
    (r'click link:^\s*View\ all\ transactions\s*$', 'Click the "View all transactions" link'),
    ('submit button:transfer|submit clicks=3', 'Click the "Transfer" button 3 times rapidly'),
    ('fill amount 0', 'Type "0" into the Amount field'),
    (r'select ^\s*(from|source)\b Everyday Checking (•••• 1042)', 'Choose "Everyday Checking (•••• 1042)" in the From field'),
    ('throttle 600ms', 'Slow every network request by 600 ms')])
def test_describe_recorded(step, text):
    assert describe_recorded(step) == text


def test_render_html_escapes_script_breakout():
    data = {'observed': '</script><script>alert(1)</script><!-- x'}
    html = render_html(data, f'<script id="d" type="application/json">{PLACEHOLDER}</script>')
    body = re.search(r'type="application/json">(.*)</script>$', html, re.S).group(1)
    assert '</script' not in body and '<!--' not in body
    assert json.loads(body) == data
    with pytest.raises(ValueError):
        render_html(data, '<html></html>')


def _run(tmp_path):
    from PIL import Image
    folder = tmp_path / 'evidence' / 'gen.labels'
    folder.mkdir(parents=True)
    Image.new('RGB', (2400, 1200), 'white').save(folder / '01-transfer-result.png')
    (folder / 'evidence.json').write_text(json.dumps({'steps': [
        {'index': 1, 'action': 'click', 'target': 'button:enter demo', 'value': None, 'ok': True}],
        'network': [{'method': 'GET', 'url': 'http://app/src/main.tsx', 'status': 200},
                    {'method': 'GET', 'url': 'http://app/dashboard', 'status': 200},
                    {'method': 'POST', 'url': 'http://api/api/transfers', 'status': 201, 'request_body': {'amount': '0'}}],
        'console': []}))
    (tmp_path / 'observations.json').write_text(json.dumps({'base_url': 'http://app', 'results': [
        {'id': 'gen.labels', 'title': 'Labels', 'area': 'GEN', 'status': 'completed', 'duration_ms': 1500, 'facts': {}}]}))
    (tmp_path / 'checks.json').write_text(json.dumps({'checks': [
        {'id': 'general.labels.readable', 'probe_id': 'gen.labels', 'expected': 'Readable labels', 'observed': 'Send'}]}))
    (tmp_path / 'verdicts.json').write_text(json.dumps({'run_tag': 't1', 'catalogue': {}, 'warnings': ['w'], 'verdicts': [
        {'requirement_id': 'GEN-001', 'area': 'GEN', 'title': 'Controls', 'status': 'failed', 'reason': None,
         'coverage': 'full', 'review': {'verdict': 'violates', 'p': .99},
         'checks': [{'id': 'general.labels.readable', 'probe_id': 'gen.labels', 'status': 'fail', 'confirmed': 'confirmed'}],
         'failure': {'probe_id': 'gen.labels', 'check_id': 'general.labels.readable', 'steps': ['click button:enter demo'],
                     'expected': 'Readable labels', 'observed': 'Send'}},
        {'requirement_id': 'GEN-002', 'area': 'GEN', 'title': 'Other', 'status': 'unverified',
         'reason': 'no_linked_checks', 'coverage': 'none', 'checks': []}],
        'cost': {'review': {'calls': 1, 'input_tokens': 100, 'output_tokens': 2, 'cost_usd': .0000042}}}))
    return tmp_path


def test_build_and_write(tmp_path):
    run, cat = _run(tmp_path), catalogue(2)
    data = build(run, cat)
    failed = data['requirements'][0]
    assert failed['failure']['steps'] == ['Click the "Enter demo" button']  # from the structured evidence steps
    assert data['probes']['gen.labels']['steps'][0]['text'] == failed['failure']['steps'][0]
    assert failed['statement'] == 'Controls are clear' and failed['checks'][0]['expected'] == 'Readable labels'
    assert data['requirements'][1]['reason_text'].startswith('No automated check')
    probe = data['probes']['gen.labels']
    assert [c['path'] for c in probe['network']] == ['/api/transfers']  # app shell and source modules dropped
    assert probe['screenshots'][0]['src'].startswith('data:image/jpeg;base64,')
    assert probe['screenshots'][0]['caption'] == 'Transfer result'
    assert data['summary']['by_area']['GEN'] == {'passed': 0, 'failed': 1, 'unverified': 1}
    assert data['cost']['phases'][0]['provider'] == 'typesafe' and data['cost']['complete']
    assert data['cost']['cold_total_usd'] == data['cost']['total_usd']  # pre-split meter: cold falls back to billed
    (run / 'cost.json').write_text(json.dumps({'entries': [], 'by_phase': {'judge': dict(
        calls=0, cached_calls=4, input_tokens=0, output_tokens=0, cost_usd=0.0, cold_cost_usd=0.0)}}))
    lower = build(run, cat, screenshots=False)['cost']
    assert not lower['complete'] and 'lower bound' in lower['note']  # cached answers without recorded usage
    (run / 'cost.json').unlink()
    assert 'GEN-001' in render_markdown(data)
    paths = write_report(run, cat)
    assert paths['html'].read_text().count('data:image/jpeg') == 1
    assert 'data:image' not in paths['json'].read_text()
