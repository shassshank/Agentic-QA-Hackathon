"""Synthetic counterexamples ensure oracles can pass on a correct build."""

from __future__ import annotations

from copy import deepcopy

import pytest
from typer.testing import CliRunner

from qa_agent.cli import app
from qa_agent.verify import evaluate
from qa_agent.verify.models import Check


def account(name='Checking', ident='•••• 1234', balance='10.00'):
    return dict(name=name, identifier_text=ident, type_label=name, text=f'{name} {ident}',
                href=f'/accounts/{name}', balance=balance, balance_text=f'${balance}', balance_decimals=2)


def row(id='r', **changes):
    return dict(dict(id=id, cells={'Description': 'memo'}, occurred_at='2025-01-02T01:00:00Z',
                     date_text='Jan 2, 2025', description='memo', type='Transfer',
                     account='Checking •••• 1234 to Savings •••• 5678', amount='1.00', amount_text='$1.00',
                     status='Completed', direction='neutral'), **changes)


def block(rows=None, accounts=None):
    rows = [row()] if rows is None else rows
    accounts = [account(), account('Savings', '•••• 5678', '20.00')] if accounts is None else accounts
    return dict(rows=deepcopy(rows), accounts=deepcopy(accounts),
                api_accounts={'accounts': deepcopy(accounts)},
                api_transactions={'transactions': [{'id': r['id'], 'amount': r['amount']} for r in rows]})


def state(**changes):
    return dict(dict(path='/', landing_enter_visible=True, header_customer=None, logout_visible=False,
                     account_cards=0, transaction_rows=0, transfer_form=False, profile_fields=False,
                     alerts=[], statuses=[]), **changes)


def transfer():
    before = block(rows=[])
    after = block(accounts=[account(balance='9.00'), account('Savings', '•••• 5678', '21.00')])
    timeline = [dict(kind='request', method='POST', t=1), dict(kind='feedback', text='Submitting…', t=2),
                dict(kind='response', method='POST', status=201, t=3),
                dict(kind='feedback', text='Transfer complete', t=4),
                dict(kind='submit-state', disabled=False, t=2)]
    return dict(plan=dict(source='Checking', destination='Savings', source_key='•••• 1234',
                          destination_key='•••• 5678', amount='1.00', memo='memo'),
                before=before, after=after, after_reload=deepcopy(after),
                source_detail=dict(detail=account(balance='9.00'), rows=[row()]),
                destination_detail=dict(detail=account('Savings', '•••• 5678', '21.00'), rows=[row()]),
                attempt=dict(posts=[{'status': 201}], feedback=dict(statuses=['Transfer complete'], alerts=[],
                             field_errors=[]), timeline=timeline, screenshot='result.png',
                             side_balances_before=[dict(label='Checking •••• 1234', balance='$10.00', amount='10.00')],
                             side_balances_after=[dict(label='Checking •••• 1234', balance='$9.00', amount='9.00')]))


def rejection():
    f = transfer()
    f['after'] = deepcopy(f['before'])
    f['attempt']['posts'] = []
    f['attempt']['feedback'] = dict(alerts=['Amount must be positive'], statuses=[], field_errors=[])
    return f


def report(pid, facts, **extra):
    return evaluate(dict(run_tag='synthetic', base_url='http://example.test', results=[
        dict(id=pid, area=pid.split('.')[0].upper(), facts=facts, status='completed', **extra)]))


def selected(pid, facts, check_id):
    return next(c for c in report(pid, facts).checks if c.id == check_id)


def change(f, path, value):
    target = f
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def cases():
    landing = dict(path='/', enter_action_visible=True, password_inputs=0, identity_inputs=0)
    enter = dict(landing=landing, after_enter=dict(path='/dashboard', account_cards=2,
                 header=dict(customer='Demo', controls=['Log out'])))
    for check, path, bad in [
        ('session.enter.landing', ('landing', 'enter_action_visible'), False),
        ('session.enter.entered', ('after_enter', 'account_cards'), 0),
        ('session.no_credentials.password_inputs', ('landing', 'password_inputs'), 1),
        ('session.no_credentials.identity_inputs', ('landing', 'identity_inputs'), 1),
        ('header.customer.label', ('after_enter', 'header', 'customer'), ''),
        ('header.logout.control', ('after_enter', 'header', 'controls'), [])]:
        yield 'ses.enter_demo', enter, check, path, bad
    yield 'ses.double_enter', {'after': state(path='/dashboard', header_customer='Demo', account_cards=2)}, \
        'general.duplicate_submit.enter', ('after', 'alerts'), ['Error']
    yield 'ses.logout', {'before': state(logout_visible=True), 'after': state()}, \
        'session.logout.access', ('after', 'profile_fields'), True
    for pid, behaviour in [('ses.no_session', 'session.protected_without_session'),
                           ('ses.protected_after_logout', 'session.protected_after_logout')]:
        yield pid, {'visits': [state(requested='/dashboard')]}, behaviour + '.0', \
            ('visits', 0, 'account_cards'), 1
    yield 'ses.refresh_keeps_session', {'visits': [state(requested='/dashboard', path='/dashboard',
        header_customer='Demo', account_cards=2)]}, 'session.refresh_keeps.0', ('visits', 0, 'path'), '/'
    for name in ('after_back', 'after_reload', 'after_direct_reload'):
        yield 'ses.refresh_after_logout', {name: state()}, 'session.refresh_after_logout.' + name, \
            (name, 'transfer_form'), True
    dashboard = block()
    for check, path, bad in [
        ('accounts.list.types', ('accounts', 1, 'type_label'), 'Other'),
        ('accounts.fields.0.name', ('accounts', 0, 'name'), ''),
        ('accounts.fields.0.identifier_text', ('accounts', 0, 'identifier_text'), ''),
        ('accounts.fields.0.type_label', ('accounts', 0, 'type_label'), ''),
        ('accounts.fields.0.balance', ('accounts', 0, 'balance_text'), 'unavailable'),
        ('accounts.fields.0.currency', ('accounts', 0, 'balance_text'), '10.00'),
        ('accounts.masking.0', ('accounts', 0, 'identifier_text'), '123456789'),
        ('accounts.two_decimals.card.0', ('accounts', 0, 'balance_text'), '$10.0'),
        ('accounts.balance_consistency.api.0', ('api_accounts', 'accounts', 0, 'balance'), '11.00')]:
        if check == 'accounts.list.types':
            d = deepcopy(dashboard)
            d['accounts'][1]['text'] = 'Other'
            yield 'acc.dashboard', d, check, path, bad
        else:
            yield 'acc.dashboard', dashboard, check, path, bad
    detail = dict(dashboard_accounts=[account()], details=[dict(account='Checking', detail=dict(
        heading='Checking', identifier_text='•••• 1234', balance='10.00', balance_text='$10.00'), rows=[row()])])
    for field, bad in [('heading', 'Other'), ('identifier_text', '•••• 9999'), ('balance', '11.00')]:
        yield 'acc.detail', detail, f'accounts.detail.0.{field}', ('details', 0, 'detail', field), bad
    yield 'acc.detail', detail, 'accounts.detail.0.history', ('details', 0, 'rows'), []
    yield 'acc.detail', detail, 'accounts.two_decimals.detail.0', ('details', 0, 'detail', 'balance_text'), '$10'
    for pid in ('trf.valid', 'trf.min_amount', 'trf.exact_balance'):
        behaviour = {'trf.valid': 'transfer.valid', 'trf.min_amount': 'transfer.amount_accept',
                     'trf.exact_balance': 'transfer.funds_boundary'}[pid]
        for suffix, path, bad in [('committed', ('attempt', 'posts'), []),
                                  ('success', ('attempt', 'feedback', 'statuses'), []),
                                  ('balances', ('after', 'accounts', 0, 'balance'), '10.00')]:
            yield pid, transfer(), behaviour + '.' + suffix, path, bad
    for check, path, bad in [
        ('transfer.atomic.deltas', ('after', 'accounts', 1, 'balance'), '20.00'),
        ('transfer.record.count', ('after', 'rows'), []),
        ('transfer.record.0.type', ('after', 'rows', 0, 'type'), 'Deposit'),
        ('transfer.record.0.status', ('after', 'rows', 0, 'status'), 'Pending'),
        ('transfer.record.0.amount', ('after', 'rows', 0, 'amount'), '2.00'),
        ('transfer.record.0.accounts', ('after', 'rows', 0, 'account'), 'Unrelated'),
        ('transfer.record.0.date', ('after', 'rows', 0, 'date_text'), ''),
        ('transfer.record.0.memo', ('after', 'rows', 0, 'description'), 'wrong'),
        ('transfer.record.0.source_detail', ('source_detail', 'rows'), []),
        ('transfer.record.0.destination_detail', ('destination_detail', 'rows'), []),
        ('transfer.success_after_commit.timeline', ('attempt', 'timeline', 3, 't'), 0),
        ('transfer.refresh_persists.balances', ('after_reload', 'accounts', 0, 'balance'), '10'),
        ('transfer.refresh_persists.record', ('after_reload', 'rows'), []),
        ('accounts.two_decimals.side.before.0', ('attempt', 'side_balances_before', 0, 'balance'), '$10'),
        ('accounts.balance_consistency.side.before.0', ('attempt', 'side_balances_before', 0, 'amount'), '99')]:
        f = transfer()
        if check.endswith('.date'):
            f['after']['rows'][0]['occurred_at'] = None
        if check.endswith('.memo'):
            f['after']['rows'][0]['cells'] = {}
        yield 'trf.valid', f, check, path, bad
    f = transfer()
    f['plan']['memo'] = None
    yield 'trf.memo_optional', f, 'transfer.memo_optional.description', ('after', 'rows', 0, 'description'), ''
    for pid, behaviour in [('trf.reject.zero', 'transfer.amount_validation.zero'),
                           ('trf.same_account', 'transfer.same_account'),
                           ('trf.insufficient', 'transfer.insufficient_funds')]:
        for suffix, path, bad in [('rejected', ('attempt', 'posts'), [{'status': 201}]),
                                  ('no_side_effect', ('after', 'rows'), [row()]),
                                  ('no_success_state', ('attempt', 'feedback', 'statuses'), ['Transfer complete']),
                                  ('explained', ('attempt', 'feedback', 'alerts'), [])]:
            yield pid, rejection(), behaviour + '.' + suffix, path, bad
    for pid in ('trf.double_submit', 'trf.slow_submit'):
        yield pid, transfer(), 'general.duplicate_submit.effects', ('after', 'rows'), []
        yield pid, transfer(), 'general.duplicate_submit.rows', ('after', 'rows'), []
    yield 'trf.slow_submit', transfer(), 'general.loading_state.submit', ('attempt', 'timeline', 1, 'text'), 'Ready'
    history = dict(accounts=[account(), account('Savings', '•••• 5678')], header={'customer': 'Demo'},
                   combined=block([row(), row('r2', type='Deposit', status='Pending', account='Checking',
                   occurred_at='2025-01-03T01:00:00Z')]), account_histories=[dict(account='Checking', **block())])
    for check, path, bad in [
        ('history.combined.union', ('account_histories', 0, 'rows'), []),
        ('history.account_specific.account_histories.0', ('account_histories', 0, 'rows', 0, 'account'), 'Other'),
        ('history.row_fields.combined', ('combined', 'rows', 0, 'date_text'), ''),
        ('history.row_amount.combined', ('combined', 'rows', 0, 'amount_text'), '$2.00'),
        ('seed.variety.customer', ('header', 'customer'), ''),
        ('seed.variety.checking', ('accounts', 0, 'type_label'), 'Other'),
        ('seed.variety.savings', ('accounts', 1, 'type_label'), 'Other'),
        ('seed.variety.dates', ('combined', 'rows', 1, 'occurred_at'), '2025-01-02T01:00:00Z'),
        ('seed.variety.transfer', ('combined', 'rows', 0, 'type'), 'Deposit'),
        ('seed.variety.type', ('combined', 'rows', 1, 'type'), 'Transfer'),
        ('seed.variety.status', ('combined', 'rows', 1, 'status'), 'Completed'),
        ('seed.variety.account', ('combined', 'rows', 1, 'account'), row()['account'])]:
        h = deepcopy(history)
        h['account_histories'][0]['rows'] = deepcopy(h['combined']['rows'])
        yield 'his.combined', h, check, path, bad
    for kind, label in [('account', 'Checking'), ('type', 'Transfer'), ('status', 'Completed')]:
        f = dict(baseline=block(), applications=[dict(option={'label': label}, rows=[row()], path='/transactions')])
        for suffix in ('predicate', 'set'):
            yield f'his.filter.{kind}', f, f'history.filter.{kind}.0.{suffix}', \
                ('applications', 0, 'rows'), [row('wrong', account='Other', type='Other', status='Other')]
    for case, kind in [('single_day_first', 'date_inclusive'), ('boundaries_inner', 'date_inclusive'),
                        ('start_only', 'date')]:
        f = dict(baseline=block(), timezone={'zone': 'America/Los_Angeles'}, applications=[dict(
            case=case, start='2025-01-01', end='2025-01-01', rows=[row()])])
        yield 'his.filter.dates', f, f'history.filter.{kind}.0.set', ('applications', 0, 'rows'), []
    yield 'his.filter.combined', dict(baseline=block(), seed_row=row(), application={'rows': [row()]}), \
        'history.filter.combined.set', ('application', 'rows'), []
    yield 'his.empty', {'application': dict(rows=[], empty_text='No results', alerts=[], error_text=None)}, \
        'history.empty_state.view', ('application', 'alerts'), ['Error']
    yield 'his.clear', dict(baseline=block(), after_clear=dict(rows=[row()], path='/transactions')), \
        'history.clear.restored', ('after_clear', 'path'), '/transactions?type=transfer'
    for check, path, bad in [('profile.fields.name', ('profile', 'name'), ''),
                              ('profile.fields.email', ('profile', 'email'), ''),
                              ('profile.masked_email.local', ('profile', 'email'), 'demo@example.test')]:
        yield 'pro.view', dict(profile={'name': 'Demo', 'email': 'd***@example.test'}), check, path, bad
    for category, bad in [('password', '<input type="password">'), ('jwt', 'eyJabc.def.ghi'),
                           ('keys', '{"secret": "value"}'), ('email', 'demo@example.test'), ('digits', '12345678')]:
        yield 'pro.no_secrets', dict(pages=[dict(text='d***@example.test', attributes=[], storage={})], api_responses=[]), \
            'profile.no_secrets.' + category, ('pages', 0, 'text'), bad
    form = dict(controls=[dict(label=label, required=False) for label in
                          ('From account', 'To account', 'Amount', 'Memo')], layout={'unlabeled_controls': []})
    for i, label in enumerate(('source', 'destination', 'amount', 'memo')):
        yield 'trf.form', form, 'transfer.form_fields.' + label, ('controls', i, 'label'), ''
    yield 'trf.form', form, 'general.labels.form', ('controls', 0, 'label'), ''
    view = dict(views=[dict(viewport={'width': 390}, path='/dashboard', visible_nav_links=1, h1=['Dashboard'],
                           layout=dict(horizontal_overflow=False, offscreen_controls=[], unlabeled_controls=[]))])
    for field, path, bad in [('horizontal_overflow', ('views', 0, 'layout', 'horizontal_overflow'), True),
                              ('offscreen_controls', ('views', 0, 'layout', 'offscreen_controls'), ['Submit']),
                              ('visible_nav_links', ('views', 0, 'visible_nav_links'), 0),
                              ('h1', ('views', 0, 'h1'), [])]:
        yield 'gen.viewports', view, 'general.viewport.0.' + field, path, bad
    yield 'gen.viewports', view, 'general.labels.0', ('views', 0, 'layout', 'unlabeled_controls'), ['Amount']
    yield 'gen.nav', {'page_links': [dict(label='Transfer from this account', account='Checking',
        selected_options=['Checking'], path='/transfer', h1=['Transfer'])]}, 'general.navigation.0.preselected', \
        ('page_links', 0, 'selected_options'), ['Savings']
    yield 'gen.loading', {'views': [dict(path='/dashboard', timeline=[
        dict(kind='feedback', text='Loading…', t=1), dict(kind='response', method='GET', url='/api/accounts', t=2)])]}, \
        'general.loading_state.0', ('views', 0, 'timeline', 0, 't'), 3
    recovery = {'views': [dict(state=state(alerts=['Unavailable']), actions=['Retry'],
                              after_retry=state(path='/dashboard', account_cards=2))]}
    yield 'gen.recoverable_error', recovery, 'general.recoverable_error.0.action', ('views', 0, 'actions'), []
    yield 'gen.recoverable_error', recovery, 'general.recoverable_error.0.retry', \
        ('views', 0, 'after_retry', 'account_cards'), 0
    yield 'gen.not_found', {'actions': ['Back to dashboard']}, 'general.not_found.action', ('actions',), []


@pytest.mark.parametrize('pid,facts,check_id,path,bad', list(cases()), ids=lambda v: v if isinstance(v, str) else None)
def test_families_pass_and_fail(pid, facts, check_id, path, bad):
    assert selected(pid, facts, check_id).status == 'pass'
    broken = deepcopy(facts)
    change(broken, path, bad)
    assert selected(pid, broken, check_id).status == 'fail'


@pytest.mark.parametrize('restore', [dict(reason='foreign transactions'), dict(window={'foreign_rows': [row()]})])
def test_interference(restore):
    facts = transfer()
    facts['restore'] = restore
    checks = report('trf.valid', facts).checks
    for c in checks:
        if c.behaviour.startswith(('history.', 'accounts.')) or c.id in (
                'transfer.valid.balances', 'transfer.atomic.deltas', 'transfer.record.count',
                'transfer.refresh_persists.balances', 'transfer.refresh_persists.record'):
            assert c.status == 'unknown'
            assert c.reason == 'concurrent activity'
    assert next(c for c in checks if c.id == 'transfer.valid.committed').status == 'pass'


@pytest.mark.parametrize('status', ['blocked', 'errored'])
def test_incomplete(status):
    observations = dict(results=[dict(id='trf.valid', area='TRF', facts=transfer(), status=status,
                                      behaviours=['transfer.valid', 'transfer.record'], error='not reachable')])
    checks = evaluate(observations).checks
    assert len(checks) == 2
    assert all(c.status == 'unknown' and 'not reachable' in c.reason for c in checks)


def test_judge_exact_text_and_no_false_success():
    facts = rejection()
    facts['attempt']['feedback']['alerts'] = ['Transfer not completed. Try a positive amount.']
    checks = report('trf.reject.zero', facts).checks
    assert next(c for c in checks if c.id.endswith('no_success_state')).status == 'pass'
    judge = next(c for c in checks if c.needs_judge)
    assert judge.status == 'unknown'
    assert judge.judge_input['text'] == facts['attempt']['feedback']['alerts']
    direction = report('his.combined', dict(combined=block([row(type='Deposit')]))).checks
    assert any(c.needs_judge and c.judge_input['text']['amount_text'] == '$1.00' for c in direction)
    nav = selected('gen.nav', {'nav': [dict(label='Explore', path='/profile', h1=['Your details'])]},
                   'general.navigation.0')
    assert nav.status == 'unknown' and nav.needs_judge


def test_missing_deterministic_and_pure():
    facts = transfer()
    original = deepcopy(facts)
    a = report('trf.valid', facts)
    assert facts == original
    assert a == report('trf.valid', facts)
    del facts['after']['accounts'][0]['balance']
    assert selected('trf.valid', facts, 'transfer.valid.balances').status == 'unknown'
    assert report('unexpected', {}).checks[0].status == 'unknown'
    with pytest.raises(ValueError):
        Check(id='x', behaviour='x', area='x', title='x', status='fail', expected='x', observed='x', probe_id='x')


def test_route_dates_and_non_route_digits():
    facts = dict(pages=[dict(text='2025-01-01T12:00:00Z', attributes=[
        '/accounts/00000000-0000-0000-0000-000000000001'])], api_responses=[])
    assert selected('pro.no_secrets', facts, 'profile.no_secrets.digits').status == 'pass'
    assert selected('pro.no_secrets', facts, 'accounts.masking.record_ids').status == 'unknown'
    # Bare UUIDs (data attributes, API ids) are record ids too, not exposed account numbers.
    facts['pages'][0]['attributes'].append('98121118-0000-4000-8000-000000000212')
    assert selected('pro.no_secrets', facts, 'profile.no_secrets.digits').status == 'pass'
    facts['pages'][0]['attributes'].append('123456789')
    assert selected('pro.no_secrets', facts, 'profile.no_secrets.digits').status == 'fail'


def test_duplicate_submit_judged_by_effect_not_request_count():
    facts = transfer()
    facts['attempt']['posts'] = [facts['attempt']['posts'][0]] * 2  # idempotent backend saw two POSTs
    assert selected('trf.double_submit', facts, 'general.duplicate_submit.effects').status == 'pass'


def test_double_submit_feeds_only_duplicate_submit_families():
    # Several applied transfers from repeated clicks are a duplicate-submit failure, not an atomicity, record or
    # success-timing failure of a single transfer.
    ids = {c.id for c in report('trf.double_submit', transfer()).checks}
    assert not any(i.startswith(('transfer.atomic', 'transfer.record', 'transfer.success_after_commit')) for i in ids)
    assert 'general.duplicate_submit.effects' in ids
    slow = {c.id for c in report('trf.slow_submit', transfer()).checks}
    assert 'transfer.atomic.deltas' in slow and 'transfer.success_after_commit.timeline' in slow


def test_atomic_requires_the_requested_amount():
    facts = transfer()
    for account in facts['after']['accounts']:
        account['balance'] = next(a['balance'] for a in facts['before']['accounts'] if a['name'] == account['name'])
    assert selected('trf.valid', facts, 'transfer.atomic.deltas').status == 'fail'  # no movement is not atomic


def test_old_memo_rows_do_not_count_as_new():
    facts = transfer()
    facts['before']['rows'] = [row('old')]
    facts['after']['rows'].append(row('old'))
    assert selected('trf.valid', facts, 'transfer.record.count').status == 'pass'


def test_cli_evidence(tmp_path):
    import json
    facts = transfer()
    observation = dict(run_tag='synthetic', base_url='example', results=[dict(
        id='trf.valid', status='completed', area='TRF', facts=facts)])
    (tmp_path / 'observations.json').write_text(json.dumps(observation))
    evidence = tmp_path / 'evidence' / 'trf.valid'
    evidence.mkdir(parents=True)
    (evidence / 'evidence.json').write_text(json.dumps(dict(steps=[{'screenshot': 'dashboard.png'}], network=[{}])))
    result = CliRunner().invoke(app, ['verify', '--run', str(tmp_path)])
    assert result.exit_code == 0, result.output
    checks = json.loads((tmp_path / 'checks.json').read_text())['checks']
    assert all('dashboard.png' in c['evidence']['screenshots'] for c in checks)
    assert all(c['evidence']['steps'] and c['evidence']['network'] for c in checks)


def test_date_open_ends_reversed_and_missing_zone():
    baseline = block([row(), row('later', occurred_at='2025-01-04T01:00:00Z')])
    for start, end, expected in [('2025-01-03', None, [baseline['rows'][1]]),
                                  (None, '2025-01-01', [baseline['rows'][0]]),
                                  ('2025-01-05', '2025-01-01', [])]:
        facts = dict(baseline=baseline, timezone={'zone': 'America/Los_Angeles'}, applications=[dict(
            case='reversed' if start and end else 'open', start=start, end=end, rows=expected)])
        assert selected('his.filter.dates', facts, 'history.filter.date.0.set').status == 'pass'
        facts['applications'][0]['rows'] = baseline['rows']
        assert selected('his.filter.dates', facts, 'history.filter.date.0.set').status == 'fail'
        facts['timezone']['zone'] = 'invalid-zone'
        assert selected('his.filter.dates', facts, 'history.filter.date.0.set').status == 'unknown'


def test_success_requires_commit_and_no_error_alert():
    facts = transfer()
    facts['attempt']['timeline'][2]['status'] = 400
    assert selected('trf.valid', facts, 'transfer.success_after_commit.timeline').status == 'fail'
    facts['attempt']['feedback']['alerts'] = ['Insufficient funds']
    assert selected('trf.valid', facts, 'transfer.valid.success').status == 'fail'
    assert selected('trf.reject.zero', facts, 'transfer.amount_validation.zero.no_success_state').status == 'fail'


def test_cell_tuple_keys_are_not_truncated():
    facts = transfer()
    a, b = row(id=None), row(id=None)
    a['cells'] = {'Description': 'x' * 800 + 'old'}
    b['cells'] = {'Description': 'x' * 800 + 'new memo'}
    facts['before']['rows'] = [a]
    facts['after']['rows'] = [a, b]
    facts['plan']['memo'] = 'new memo'
    assert selected('trf.valid', facts, 'transfer.record.count').status == 'pass'
