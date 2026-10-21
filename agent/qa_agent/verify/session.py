"""Access checks require both an access state and absence of protected content."""

from __future__ import annotations

import re

from .util import Builder, content, protected, text


def evaluate(b: Builder) -> None:
    f, pid = b.facts, b.probe['id']
    if not pid.startswith('ses.'):
        return
    if pid == 'ses.enter_demo':
        landing, after = f.get('landing', {}), f.get('after_enter', {})
        b.add('session.enter', 'landing', landing.get('enter_action_visible'), 'enter action visible', landing)
        b.add('session.enter', 'entered', (after['path'] != landing.get('path') and after['account_cards'] > 0)
              if 'path' in after and 'account_cards' in after else None, 'protected path and account cards', after)
        for field in ('password_inputs', 'identity_inputs'):
            b.add('session.no_credentials', field, landing[field] == 0 if field in landing else None,
                  'zero credential inputs', landing.get(field))
        header = after.get('header', {})
        b.add('header.customer', 'label', bool(header['customer']) if 'customer' in header else None,
              'non-empty customer', header.get('customer'))
        b.add('header.logout', 'control', bool(re.search(r'log ?out|sign ?out', text(header['controls']), re.I))
              if 'controls' in header else None, 'logout control', header.get('controls'))
    if pid == 'ses.double_enter':
        s = f.get('after', {})
        b.add('general.duplicate_submit', 'enter', bool(s.get('header_customer')) and protected(s) is True
              and not s.get('alerts') if s else None, 'protected view without alert', s)
    if pid == 'ses.logout':
        s = f.get('after', {})
        b.add('session.logout', 'access', s.get('landing_enter_visible') and protected(s) is False if s else None,
              'landing visible, no protected content', s)
        b.add('header.logout', 'before', f.get('before', {}).get('logout_visible'), 'logout visible before click',
              f.get('before', {}).get('logout_visible'))
    visits = f.get('visits', [])
    for i, s in enumerate(visits):
        if pid == 'ses.refresh_keeps_session':
            ok = s.get('path') == s.get('requested') and bool(s.get('header_customer')) and content(s)
            behaviour = 'session.refresh_keeps'
        else:
            ok = protected(s) is False and access_denied(s)
            behaviour = 'session.protected_without_session' if pid == 'ses.no_session' else 'session.protected_after_logout'
        b.add(behaviour, str(i), ok, 'requested content retained' if 'refresh_keeps' in behaviour
              else 'no protected content and landing/access message', s)
    for name in ('after_back', 'after_reload', 'after_direct_reload'):
        if name in f:
            s = f[name]
            p = protected(s)
            # Visible protected content is a failure even mid-load; a pass needs a settled, terminal denied state,
            # so a page still loading (which might reveal content later) stays unknown.
            ok = (False if p is True else
                  True if p is False and s.get('settled') is not False and access_denied(s) else None)
            b.add('session.refresh_after_logout', name, ok, 'no protected content and landing/access message', s)


def access_denied(s: dict) -> bool:
    return bool(s.get('landing_enter_visible') or re.search(
        r'access required|enter.*demo|sign in|log in|unauthori', text(s.get('alerts', []) + s.get('statuses', [])), re.I))
