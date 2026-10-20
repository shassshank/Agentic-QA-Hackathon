"""Account expectations come from labels and balances in the same probe."""

from __future__ import annotations

import re

from ..observe.money import decimal_places, parse_money
from .util import Builder, account_key, number, records, text


def evaluate(b: Builder) -> None:
    f = b.facts
    accounts = f.get('accounts', f.get('dashboard_accounts', []))
    if accounts and isinstance(accounts[0], str):
        accounts = []
    if b.probe['id'] == 'acc.dashboard':
        b.add('accounts.list', 'types', bool(re.search(r'check|current', text([{'type': a.get('type_label'), 'text': a.get('text')} for a in accounts]), re.I)) and
              bool(re.search('sav', text([{'type': a.get('type_label'), 'text': a.get('text')} for a in accounts]), re.I)) if 'accounts' in f else None,
              'checking and savings accounts', [a.get('type_label') for a in accounts])
        for i, a in enumerate(accounts):
            for field in ('name', 'identifier_text', 'type_label'):
                b.add('accounts.fields', f'{i}.{field}', bool(a[field]) if field in a else None,
                      f'non-empty {field}', a.get(field))
            value = a.get('balance_text')
            b.add('accounts.fields', f'{i}.balance', parse_money(value) is not None if value is not None else None,
                  'parseable balance', value, sensitive=True)
            b.add('accounts.fields', f'{i}.currency', bool(re.search(r'\$|USD', value)) if value is not None else None,
                  'USD indicated', value)
            ident = a.get('identifier_text')
            b.add('accounts.masking', str(i), bool(re.search(r'[^\d\s\w-]{2,}|[xX]{2,}', ident)) and
                  len(re.findall(r'\d', ident)) <= 6 if ident is not None else None,
                  'masked identifier with at most six digits', ident)
    for i, a in enumerate(accounts):
        value = a.get('balance_text')
        b.add('accounts.two_decimals', f'card.{i}', decimal_places(value) == 2 if value is not None else None,
              'two decimal places', value, sensitive=True)
        api = records(f.get('api_accounts'), 'accounts')
        if api is not None:
            match = next((x for x in api if x.get('name') == a.get('name') or
                          (x.get('id') and (a.get('href') or '').endswith(x['id']))), None)
            b.add('accounts.balance_consistency', f'api.{i}', number(a.get('balance')) == number(match.get('balance'))
                  if match and number(a.get('balance')) is not None and number(match.get('balance')) is not None
                  else None, 'dashboard equals API balance', {'ui': a.get('balance'), 'api': match}, sensitive=True)
    for i, entry in enumerate(f.get('details', [])):
        a = next((a for a in accounts if a.get('name') == entry.get('account')), None)
        d = entry.get('detail', {})
        for field, source in [('heading', 'name'), ('identifier_text', 'identifier_text'), ('balance', 'balance')]:
            lhs, rhs = d.get(field), a.get(source) if a else None
            if field == 'balance':
                lhs, rhs = number(lhs), number(rhs)
            b.add('accounts.detail', f'{i}.{field}', lhs == rhs if lhs is not None and rhs is not None else None,
                  f'detail {field} equals dashboard', {'detail': lhs, 'dashboard': rhs}, sensitive=field == 'balance')
        balance, dashboard = number(d.get('balance')), number(a.get('balance')) if a else None
        b.add('accounts.balance_consistency', f'detail.{i}', balance == dashboard
              if balance is not None and dashboard is not None else None,
              'detail balance equals dashboard', {'detail': balance, 'dashboard': dashboard}, sensitive=True)
        b.add('accounts.detail', f'{i}.history', bool(entry.get('rows') or entry.get('empty_text'))
              if 'rows' in entry else None, 'history or empty state', {'rows': len(entry.get('rows', [])),
                                                                     'empty': entry.get('empty_text')}, sensitive=True)
        b.add('accounts.two_decimals', f'detail.{i}', decimal_places(d.get('balance_text')) == 2
              if d.get('balance_text') is not None else None, 'two decimal places', d.get('balance_text'), sensitive=True)
    for stage in ('before', 'after'):
        snap = f.get(stage, {})
        api = records(snap.get('api_accounts'), 'accounts')
        for i, a in enumerate(snap.get('accounts', [])):
            match = next((x for x in api or [] if x.get('name') == a.get('name')), None)
            b.add('accounts.balance_consistency', f'{stage}.api.{i}', number(a.get('balance')) == number(match.get('balance'))
                  if match and number(a.get('balance')) is not None and number(match.get('balance')) is not None
                  else None, 'snapshot balance equals API', {'ui': a.get('balance'), 'api': match}, sensitive=True)
    attempt = f.get('attempt', {})
    for stage, snap_name in [('before', 'side_balances_before'), ('after', 'side_balances_after')]:
        side = attempt.get(snap_name, [])
        for i, a in enumerate(side):
            value = a.get('balance')
            b.add('accounts.two_decimals', f'side.{stage}.{i}', decimal_places(value) == 2 if value is not None else None,
                  'two decimal places', value, sensitive=True)
            match = next((x for x in f.get(stage, {}).get('accounts', []) if account_key(x) in a.get('label', '')), None)
            b.add('accounts.balance_consistency', f'side.{stage}.{i}', number(a.get('amount')) == number(match.get('balance'))
                  if match and number(a.get('amount')) is not None and number(match.get('balance')) is not None else None,
                  'transfer side balance equals dashboard', {'side': a.get('balance'), 'dashboard': match}, sensitive=True)
    for name, plan_name in [('source_detail', 'source'), ('destination_detail', 'destination')]:
        if name in f:
            detail = f[name].get('detail', {})
            match = next((a for a in f.get('after', {}).get('accounts', [])
                          if a.get('name') == f.get('plan', {}).get(plan_name)), None)
            lhs, rhs = number(detail.get('balance')), number(match.get('balance')) if match else None
            b.add('accounts.balance_consistency', name, lhs == rhs if lhs is not None and rhs is not None else None,
                  'detail balance equals after dashboard', {'detail': lhs, 'dashboard': rhs}, sensitive=True)
            value = detail.get('balance_text')
            b.add('accounts.two_decimals', name, decimal_places(value) == 2 if value is not None else None,
                  'two decimal places', value, sensitive=True)
