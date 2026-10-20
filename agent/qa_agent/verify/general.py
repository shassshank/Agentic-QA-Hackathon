"""UI checks use measured layout, visible labels and recorded timing."""

from __future__ import annotations

import re

from .util import BUSY, Builder, content, text


def words(value: str) -> set[str]:
    synonyms = {'funds': 'transfer', 'history': 'transactions', 'home': 'dashboard'}
    return {synonyms.get(w, w) for w in re.findall(r'[a-z]+', value.lower())}


def evaluate(b: Builder) -> None:
    f, pid = b.facts, b.probe['id']
    if pid == 'trf.form':
        controls = f.get('controls')
        b.add('general.labels', 'form', all(c.get('label') for c in controls) if controls is not None else None,
              'all transfer controls labeled', controls)
        layout = f.get('layout', {})
        b.add('general.labels', 'layout', not layout['unlabeled_controls'] if 'unlabeled_controls' in layout else None,
              'no unlabeled controls', layout.get('unlabeled_controls'))
    if pid == 'gen.viewports':
        for i, v in enumerate(f.get('views', [])):
            layout = v.get('layout', {})
            for field, expected in [('horizontal_overflow', False), ('offscreen_controls', []),
                                    ('visible_nav_links', True), ('h1', True)]:
                source = layout if field in ('horizontal_overflow', 'offscreen_controls') else v
                value = source.get(field)
                ok = (bool(value) if expected is True else value == expected) if field in source else None
                b.add('general.viewport', f'{i}.{field}', ok, f'{field}: {expected}',
                      {'viewport': v.get('viewport'), 'path': v.get('path'), 'value': value})
            b.add('general.labels', str(i), not layout['unlabeled_controls'] if 'unlabeled_controls' in layout else None,
                  'no unlabeled controls', layout.get('unlabeled_controls'))
    if pid == 'gen.nav':
        for i, link in enumerate(f.get('nav', []) + f.get('page_links', [])):
            overlap = words(link.get('label', '')) & words(link.get('path', '') + ' ' + text(link.get('h1', [])))
            if overlap:
                b.add('general.navigation', str(i), True, 'label words overlap destination', link)
            else:
                b.judge('general.navigation', str(i), {'label': link.get('label'), 'path': link.get('path'),
                        'h1': link.get('h1')}, 'readable label describes destination')
            if re.search(r'transfer from this account', link.get('label', ''), re.I):
                selected = link.get('selected_options')
                b.add('general.navigation', f'{i}.preselected', bool(selected and link.get('account') and
                      link['account'] in selected[0]) if selected is not None else None,
                      'source account preselected', link)
    if pid == 'gen.loading':
        for i, v in enumerate(f.get('views', [])):
            timeline = v.get('timeline')
            suffix = '/transactions' if v.get('path') == '/transactions' else '/me' if v.get('path') == '/profile' else '/accounts'
            responses = [e['t'] for e in timeline or [] if e.get('kind') == 'response' and
                         e.get('method') == 'GET' and e.get('url', '').split('?')[0].endswith(suffix)]
            busy = [e for e in timeline or [] if e.get('kind') == 'feedback' and BUSY.search(e.get('text', ''))]
            b.add('general.loading_state', str(i), any(e['t'] < min(responses) for e in busy)
                  if responses else None, 'busy feedback before view data response', {'path': v.get('path'), 'busy': busy})
    if pid in ('gen.recoverable_error', 'acc.unknown_account'):
        views = f.get('views', [f] if 'state' in f else [])
        for i, v in enumerate(views):
            state, actions = v.get('state', {}), v.get('actions')
            b.add('general.recoverable_error', f'{i}.action', bool(state.get('alerts')) and
                  bool(re.search(r'retry|try again|dashboard|home', text(actions), re.I)) if actions is not None else None,
                  'alert and retry or dashboard action', {'alerts': state.get('alerts'), 'actions': actions})
            if 'after_retry' in v:
                after = v.get('after_retry')
                b.add('general.recoverable_error', f'{i}.retry', content(after) if after else None,
                      'content present after retry', after, reason='retry was not exercised' if after is None else None)
            elif pid == 'acc.unknown_account':
                b.add('general.recoverable_error', f'{i}.retry', None, 'recovery action works', None,
                      reason='unknown-account probe records actions but does not exercise recovery')
    if pid == 'gen.not_found':
        actions = f.get('actions')
        b.add('general.not_found', 'action', bool(re.search(r'dashboard|home', text(actions), re.I))
              if actions is not None else None, 'action back to dashboard/home', actions)
