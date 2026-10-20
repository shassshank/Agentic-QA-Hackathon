"""Seed variety is measured from the observed customer and history."""

from __future__ import annotations

import re

from ..observe.money import local_date
from .util import Builder, text


def evaluate(b: Builder) -> None:
    f = b.facts
    if b.probe['id'] != 'his.combined':
        return
    accounts, rows = f.get('accounts'), f.get('combined', {}).get('rows')
    header = f.get('header', {})
    b.add('seed.variety', 'customer', bool(header['customer']) if 'customer' in header else None,
          'one active customer label', header.get('customer'), area='seed')
    for name, pattern in [('checking', r'check|current'), ('savings', 'sav')]:
        count = sum(bool(re.search(pattern, a.get('type_label') or a.get('text', ''), re.I)) for a in accounts or [])
        b.add('seed.variety', name, count == 1 if accounts is not None else None,
              f'exactly one {name} account', count, area='seed')
    dates = {local_date(r.get('occurred_at'), f.get('timezone', {}).get('zone', 'UTC')) for r in rows or []}
    dates.discard(None)
    b.add('seed.variety', 'dates', len(dates) >= 2 if rows is not None else None,
          'at least two dates', sorted(dates), area='seed', sensitive=True)
    transfer = any((r.get('type') or '').lower() == 'transfer' and all(
        (a.get('identifier_text') or a.get('name', '')) in (r.get('account') or '') for a in accounts or [])
        for r in rows or [])
    b.add('seed.variety', 'transfer', transfer if rows is not None and accounts else None,
          'transfer between owned accounts', transfer, area='seed', sensitive=True)
    for field in ('type', 'status', 'account'):
        values = {r[field] for r in rows or [] if r.get(field)}
        b.add('seed.variety', field, len(values) >= 2 if rows is not None else None,
              f'two distinct {field} values', sorted(values), area='seed', sensitive=True)
