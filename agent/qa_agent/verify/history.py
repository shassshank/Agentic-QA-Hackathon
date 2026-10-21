"""History filters are compared with the unfiltered rows observed by that probe."""

from __future__ import annotations

import re
from datetime import date
from zoneinfo import ZoneInfoNotFoundError

from ..observe.money import local_date, parse_money
from .util import Builder, key, keys, number, records


def observed_date(iso: str | None, zone: str) -> date | None:
    try:
        return local_date(iso, zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


# When the UI states no timezone, UTC is only assumed: a date comparison counts only if it holds at both extremes.
EXTREME_ZONES = ('Etc/GMT+12', 'UTC', 'Etc/GMT-14')


def rows_in_range(rows: list[dict], start: date | None, end: date | None, zone: str, assumed: bool) -> list[dict] | None:
    """Rows whose local date lies in [start, end]; None when the answer depends on an unstated timezone."""
    results = []
    for z in (EXTREME_ZONES if assumed else (zone,)):
        days = [observed_date(r.get('occurred_at'), z) for r in rows]
        if any(d is None for d in days):
            return None
        results.append([r for r, d in zip(rows, days) if (start is None or d >= start) and (end is None or d <= end)])
    return results[0] if all(keys(r) == keys(results[0]) for r in results) else None


def account_matches(label: str, value: str) -> bool:
    if not label:
        return False
    if label.casefold() in value.casefold():
        return True
    identifiers = re.findall(r'[•●*xX]{2,}\s*-?\s*\d{1,6}', label)
    return bool(identifiers) and any(i in value for i in identifiers)


def evaluate(b: Builder) -> None:
    f, pid = b.facts, b.probe['id']
    blocks = []
    for name in ('combined', 'baseline', 'before', 'after', 'source_detail', 'destination_detail', 'after_reload'):
        if isinstance(f.get(name), dict) and 'rows' in f[name]:
            blocks.append((name, f[name]))
    for name in ('details', 'account_histories'):
        blocks.extend((f'{name}.{i}', block) for i, block in enumerate(f.get(name, [])))
    # Row-level checks run where history is the subject (combined/account views and the transfer's own
    # account histories); the same rows re-read inside every other probe's snapshots would only repeat them.
    row_level = pid in ('his.combined', 'acc.detail')
    for name, block in blocks:
        if not row_level and name not in ('source_detail', 'destination_detail'):
            continue
        rows = block.get('rows', [])
        api = records(block.get('api_transactions'), 'transactions')
        api_by_id = {t.get('id'): t for t in api or []}
        account = block.get('account')
        if name in ('source_detail', 'destination_detail'):
            account = f.get('plan', {}).get('source' if name == 'source_detail' else 'destination')
        # Zero rows inspected proves nothing about row content: unknown, not a vacuous pass. Whether a history should
        # have rows at all is checked separately (e.g. accounts.detail.N.history, transfer.record.*).
        if account:
            b.add('history.account_specific', name,
                  all(account_matches(account, r.get('account') or '') for r in rows) if rows else None,
                  'every row mentions account', {'account': account, 'mismatches': [key(r) for r in rows
                   if not account_matches(account, r.get('account') or '')]}, sensitive=True)
        bad = [key(r) for r in rows if not all(r.get(k) for k in (
            'date_text', 'description', 'type', 'account', 'amount_text', 'status'))]
        b.add('history.row_fields', name, not bad if rows else None, 'all six row fields non-empty', {'missing': bad},
              sensitive=True)
        mismatches, missing, compared = [], [], []
        for i, row in enumerate(rows):
            match = api_by_id.get(row.get('id'))
            ui, amount = parse_money(row.get('amount_text')), number(match.get('amount')) if match else None
            observed = {'id': row.get('id'), 'UI': row.get('amount_text'),
                        'API': match.get('amount') if match else None, 'status': row.get('status')}
            compared.append(observed)
            if ui is None or amount is None:
                missing.append(observed)
            elif abs(ui) != abs(amount):
                mismatches.append(observed)
            # The PRD names deposits, withdrawals and transfers; transfers need a judged direction too (from/to or
            # signed per-account amounts), otherwise "direction is clear" would pass on two of three types.
            if (row.get('status') or '').lower() == 'completed' and (row.get('type') or '').lower() in (
                    'deposit', 'withdrawal', 'transfer'):
                # Suffix by transaction id, not row position, so ids stay stable as history grows between runs.
                b.judge('history.amount_direction', f'{name}.{(row.get("id") or str(i))[-12:]}', {
                        'amount_text': row.get('amount_text'), 'account': row.get('account'),
                        'type': row.get('type'), 'status': row.get('status'), 'description': row.get('description')},
                        'completed deposits credit; completed withdrawals debit; direction clear')
        statuses = sorted({m['status'] or '?' for m in mismatches})
        b.add('history.row_amount', name, False if mismatches else None if missing or api is None else True,
              'UI amount magnitudes equal API', mismatches or missing or compared[:2], sensitive=True,
              reason=(f"{len(mismatches)} row(s) show an amount different from the transaction amount "
                      f"(statuses: {', '.join(statuses)})") if mismatches else None)
    if pid == 'his.combined':
        combined = f.get('combined', {}).get('rows')
        histories = f.get('account_histories')
        b.add('history.combined', 'union', bool(combined) and keys(combined) == set().union(
              *(keys(h.get('rows', [])) for h in histories)) if combined is not None and histories is not None else None,
              'non-empty combined history equals account history union', {'combined': len(combined or []),
              'union': len(set().union(*(keys(h.get('rows', [])) for h in histories or [])))}, sensitive=True)
    baseline = f.get('baseline', {}).get('rows')
    for i, app in enumerate(f.get('applications', [])):
        kind = pid.removeprefix('his.filter.')
        expected = None
        if kind in ('account', 'type', 'status') and baseline is not None:
            label = app.get('option', {}).get('label', '')
            def predicate(r: dict) -> bool:
                return account_matches(label, r.get('account') or '') if kind == 'account' else (
                    (r.get(kind) or '').casefold() == label.casefold())
            expected = [r for r in baseline if predicate(r)]
            b.add(f'history.filter.{kind}', f'{i}.predicate', all(predicate(r) for r in app.get('rows', [])),
                  f'every row satisfies {label}', [key(r) for r in app.get('rows', []) if not predicate(r)], sensitive=True)
        elif kind == 'dates' and baseline is not None:
            zone = f.get('timezone', {}).get('zone')
            if zone:
                start = date.fromisoformat(app['start']) if app.get('start') else None
                end = date.fromisoformat(app['end']) if app.get('end') else None
                expected = rows_in_range(baseline, start, end, zone, f['timezone'].get('assumed', False))
            kind = 'date_inclusive' if app.get('case', '').startswith(('single_day', 'boundaries')) else 'date'
        actual = app.get('rows')
        ok = keys(actual) == keys(expected) if actual is not None and expected is not None else None
        if app.get('case') == 'reversed' and actual and (app.get('error_text') or app.get('alerts')):
            b.judge('history.filter.date', f'{i}.explanation',
                    {'error_text': app.get('error_text'), 'alerts': app.get('alerts')},
                    'explanation for reversed date range')
            ok = None
        context = f"{app.get('case', '')} {app.get('start') or 'open'}..{app.get('end') or 'open'}" if 'case' in app else ''
        b.add(f'history.filter.{kind}', f'{i}.set', ok, 'filtered set equals baseline predicate; ' + context,
              {'expected': sorted(keys(expected)) if expected is not None else None,
               'actual': sorted(keys(actual)) if actual is not None else None, 'url': app.get('path')},
              reason=f'row set mismatch for {context}' if ok is False else None, sensitive=True)
    if pid == 'his.filter.combined':
        seed, app = f.get('seed_row', {}), f.get('application', {})
        dates = f.get('dates') or {}
        start = date.fromisoformat(dates['start']) if dates.get('start') else None
        end = date.fromisoformat(dates['end']) if dates.get('end') else None

        matching = [r for r in baseline or [] if (r.get('type') or '').casefold() == (seed.get('type') or '').casefold()
                    and (r.get('status') or '').casefold() == (seed.get('status') or '').casefold()
                    and account_matches(seed.get('account') or '', r.get('account') or '')]
        expected = (rows_in_range(matching, start, end, dates.get('zone') or 'UTC', dates.get('assumed', False))
                    if dates else matching)
        b.add('history.filter.combined', 'set', bool(expected) and keys(app['rows']) == keys(expected)
              if baseline is not None and seed and 'rows' in app and expected is not None else None,
              'non-empty baseline intersection of account/type/status' + ('/date range' if dates else ''),
              {'expected': sorted(keys(expected)) if expected is not None else None,
               'actual': sorted(keys(app.get('rows', []))), 'dates': dates},
              sensitive=True)
        # Without the date dimension the combination test is incomplete; say so instead of implying coverage.
        b.add('history.filter.combined', 'dimensions', bool(dates) if seed else None,
              'account, type, status and date range applied together', {'dates': dates})
    if pid == 'his.empty':
        a = f.get('application', {})
        b.add('history.empty_state', 'view', not a['rows'] and bool(a.get('empty_text')) and
              not a.get('error_text') and not a.get('alerts') if 'rows' in a else None,
              'no rows, empty text, no errors or alerts', a, sensitive=True)
    if 'after_clear' in f:
        a = f['after_clear']
        b.add('history.clear', 'restored', keys(a['rows']) == keys(baseline) and '?' not in a['path']
              if baseline is not None and 'rows' in a and 'path' in a else None,
              'baseline rows restored and no query', {'rows': len(a.get('rows', [])), 'path': a.get('path')}, sensitive=True)
