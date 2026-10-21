"""Transfers are attributed using new row identities, never old run tags."""

from __future__ import annotations

import re

from .util import BUSY, Builder, balances, key, keys, number, success, success_text, text


def evaluate(b: Builder) -> None:
    f, pid = b.facts, b.probe['id']
    if pid == 'trf.form':
        controls = f.get('controls', [])
        for label, pattern in [('source', 'from|source'), ('destination', 'to|destination'),
                               ('amount', 'amount'), ('memo', 'memo|note|reference')]:
            found = next((c for c in controls if re.search(pattern, c.get('label') or '', re.I)), None)
            b.add('transfer.form_fields', label, bool(found) if 'controls' in f else None,
                  f'{label} control present', found)
        memo = next((c for c in controls if re.search('memo|note|reference', c.get('label') or '', re.I)), None)
        b.add('transfer.memo_optional', 'form', not memo.get('required') if memo else None,
              'memo not required', memo)
        return
    if 'plan' not in f:
        return
    plan, attempt = f['plan'], f.get('attempt', {})
    before, after = f.get('before', {}), f.get('after', {})
    bb, ab = balances(before), balances(after)
    readable = bool(bb) and bb.keys() == ab.keys() and all(v is not None for v in [*bb.values(), *ab.values()])
    delta = {k: ab[k] - bb[k] for k in bb} if readable else {}
    rows = [r for r in after.get('rows', []) if key(r) not in keys(before.get('rows', []))]
    memo = plan.get('memo')
    matching = [r for r in rows if memo in text(r)] if memo else [r for r in rows if (r.get('type') or '').lower() == 'transfer']
    posts = attempt.get('posts', [])
    committed = any(200 <= (p.get('status') or 0) < 300 for p in posts)
    feedback = attempt.get('feedback', {})
    reject = pid.startswith('trf.reject.') or pid in ('trf.same_account', 'trf.insufficient')
    if reject:
        behaviour = ('transfer.amount_validation.' + pid.rsplit('.', 1)[-1] if pid.startswith('trf.reject.')
                     else 'transfer.same_account' if pid == 'trf.same_account' else 'transfer.insufficient_funds')
        b.add(behaviour, 'rejected', not committed if 'posts' in attempt else None, 'no 2xx POST', posts)
        b.add(behaviour, 'no_side_effect', not any(delta.values()) and not rows if readable and
              'rows' in before and 'rows' in after else None, 'all balances unchanged and no new rows',
              {'deltas': delta, 'new_rows': [key(r) for r in rows]}, sensitive=True)
        b.add(behaviour, 'no_success_state', not any(success_text(s) for s in feedback.get('statuses', []) + feedback.get('alerts', [])) if 'feedback' in attempt else None,
              'no success feedback', feedback)
        # A field's describedby text mixes always-visible format hints with the error; judge what the rejection added.
        hints = set(attempt.get('field_hints') or [])
        def field_message(e: dict) -> str:
            added = [p for p in e.get('parts') or [] if p not in hints]
            return ' '.join(added) or e.get('message', '')
        messages = [field_message(e) for e in feedback.get('field_errors', [])]
        visible = feedback.get('alerts', []) + feedback.get('statuses', []) + messages
        errors = feedback.get('alerts', []) + [s for s in feedback.get('statuses', []) if not success_text(s)] + messages
        b.add(behaviour, 'explained', bool(errors) if 'feedback' in attempt else None,
              'visible rejection explanation', visible)
        if errors:
            b.judge(behaviour, 'explanation_quality', visible, 'clear explanation of rejection')
        return
    amount = number(plan.get('amount'))
    source, dest = plan.get('source_key'), plan.get('destination_key')
    # Duplicate-submit and slow-network probes click repeatedly or under latency; their extra clicks legitimately
    # meet the cleared form's validation, so they feed only the duplicate/loading families, not transfer.valid.
    accept = pid in ('trf.valid', 'trf.memo_optional', 'trf.min_amount', 'trf.exact_balance')
    for behaviour in ((['transfer.valid'] if accept else []) +
                      (['transfer.amount_accept'] if pid in ('trf.min_amount', 'trf.exact_balance') else []) +
                      (['transfer.funds_boundary'] if pid == 'trf.exact_balance' else [])):
        b.add(behaviour, 'committed', committed if 'posts' in attempt else None, '2xx POST', posts)
        b.add(behaviour, 'success', success(feedback) if 'feedback' in attempt else None,
              'success feedback without error alert', feedback)
        expected = {k: -amount if k == source else amount if k == dest else number('0') for k in bb} if amount is not None else {}
        b.add(behaviour, 'balances', delta == expected if readable and amount is not None
              and source in bb and dest in bb else None, 'source -amount, destination +amount, others unchanged',
              {'expected': expected, 'deltas': delta}, sensitive=True)
    # The double-submit probe's repeated clicks are the behaviour under test: if they apply several transfers, that is
    # a duplicate-submit failure, not a non-atomic transfer or a bad single record, so its effects feed only
    # general.duplicate_submit (found by the mutant evaluation: they had failed TRF atomicity and success timing).
    repeated = pid == 'trf.double_submit'
    # Both sides must move by exactly the requested amount: equal-and-opposite alone also holds for no movement
    # or for a wrong amount. (Intermediate partial states are not observable here; that part stays unclaimed.)
    if not repeated:
        b.add('transfer.atomic', 'deltas', amount is not None and delta[source] == -amount and delta[dest] == amount and
              all(v == 0 for k, v in delta.items() if k not in (source, dest))
              if readable and source in delta and dest in delta else None,
              'source -amount and destination +amount together, others unchanged',
              {'amount': amount, 'deltas': delta}, sensitive=True)
        b.add('transfer.record', 'count', len(matching) == 1 if 'rows' in before and 'rows' in after else None,
              'exactly one new attempt row', [key(r) for r in matching], sensitive=True)
    for i, row in enumerate([] if repeated else matching or rows):
        conditions = {
            'type': (row.get('type') or '').lower() == 'transfer',
            'status': (row.get('status') or '').lower() == 'completed',
            'amount': number(row.get('amount')) == amount if number(row.get('amount')) is not None else None,
            'accounts': source in (row.get('account') or '') and dest in (row.get('account') or '')
            if source and dest else None,
            'date': bool(row.get('date_text') or row.get('occurred_at')),
            'memo': memo in text(row) if memo else bool(row.get('description')),
        }
        for field, ok in conditions.items():
            b.add('transfer.record', f'{i}.{field}', ok, f'correct transfer {field}', row, sensitive=True)
        for detail_name in ('source_detail', 'destination_detail'):
            if detail_name in f:
                b.add('transfer.record', f'{i}.{detail_name}', key(row) in keys(f[detail_name].get('rows', [])),
                      'attempt row in account history', key(row), sensitive=True)
    if pid == 'trf.memo_optional':
        b.add('transfer.memo_optional', 'description', committed and not memo and len(matching) == 1 and
              bool(matching[0].get('description')) if 'rows' in after else None,
              'empty memo accepted with non-empty row description', matching, sensitive=True)
    timeline = attempt.get('timeline')
    if repeated:
        pass
    elif timeline is not None:
        # Correlate with this attempt's own committed POST, and require that the first read after the success
        # message already shows the transfer persisted (balances moved by the amount).
        ours = {p.get('url') for p in posts if 200 <= (p.get('status') or 0) < 300}
        responses = [e['t'] for e in timeline if e.get('kind') == 'response' and e.get('method') == 'POST'
                     and e.get('url') in ours and 200 <= (e.get('status') or 0) < 300]
        successes = [e['t'] for e in timeline if e.get('kind') == 'feedback' and success_text(e.get('text', ''))]
        persisted = (delta.get(source) == -amount and delta.get(dest) == amount) if readable and amount is not None else None
        ordered = bool(responses and successes) and min(successes) >= min(responses)
        b.add('transfer.success_after_commit', 'timeline',
              None if persisted is None and ordered else ordered and bool(persisted),
              'first success after this attempt\'s 2xx response, and the next read shows balances updated',
              {'responses': responses, 'successes': successes, 'persisted_in_next_read': persisted}, sensitive=True)
    else:
        b.add('transfer.success_after_commit', 'timeline', None, 'success follows commit', None)
    if 'after_reload' in f:
        reload = f['after_reload']
        rb = balances(reload)
        b.add('transfer.refresh_persists', 'balances', rb == ab if readable and rb and
              all(v is not None for v in rb.values()) else None, 'reload balances equal after', rb, sensitive=True)
        b.add('transfer.refresh_persists', 'record', bool(memo and any(memo in text(r) for r in reload.get('rows', [])))
              if 'rows' in reload and memo else None, 'memo row survives reload', reload.get('memo_rows'), sensitive=True)
    if pid in ('trf.double_submit', 'trf.slow_submit'):
        # Judged by effect, not request count: an idempotent backend may receive several POSTs and still apply one.
        b.add('general.duplicate_submit', 'effects', len(matching) == 1 and delta.get(source) == -amount
              and delta.get(dest) == amount if readable and amount is not None and 'rows' in after else None,
              'one transfer applied (one new row, balances moved once)',
              {'posts': len(posts), 'new_rows': len(matching), 'deltas': delta}, sensitive=True)
        b.add('general.duplicate_submit', 'rows', len(matching) == 1 if 'rows' in after else None,
              'exactly one new memo row', [key(r) for r in matching], sensitive=True)
    if pid == 'trf.slow_submit':
        requests = [e['t'] for e in timeline or [] if e.get('kind') == 'request' and e.get('method') == 'POST']
        responses = [e['t'] for e in timeline or [] if e.get('kind') == 'response' and e.get('method') == 'POST']
        busy = [e for e in timeline or [] if (e.get('kind') == 'feedback' and BUSY.search(e.get('text', '')))
                or (e.get('kind') == 'submit-state' and e.get('disabled'))]
        disabled_observed = any(e.get('kind') == 'submit-state' for e in timeline or [])
        observed_busy = any(min(requests) <= e['t'] <= min(responses) for e in busy) if requests and responses else None
        condition = observed_busy if observed_busy or disabled_observed else None
        b.add('general.loading_state', 'submit', condition,
              'busy or disabled submit between request and response', busy,
              reason='timeline does not record submit disabled state' if condition is None and timeline else None)
