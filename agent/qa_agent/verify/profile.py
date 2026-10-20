"""Secret scanning preserves source paths and separates route ids from account numbers."""

from __future__ import annotations

import json
import re
from typing import Any

from .util import Builder

MASK = re.compile(r'[*•●…]|\.{3}|[xX]{2,}')
EMAIL = re.compile(r'[\w.+*•…-]+@[\w.-]+\.[A-Za-z]{2,}')
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
DATE = r'\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?'


def leaves(value: Any, path: str = '') -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [(p, s) for k, v in value.items() for p, s in leaves(v, f'{path}.{k}')]
    if isinstance(value, list):
        return [(p, s) for i, v in enumerate(value) for p, s in leaves(v, f'{path}[{i}]')]
    return [(path, value)] if isinstance(value, str) else []


def evaluate(b: Builder) -> None:
    f = b.facts
    if 'profile' in f:
        p = f['profile']
        for field in ('name', 'email'):
            b.add('profile.fields', field, bool(p[field]) if field in p else None,
                  f'non-empty {field}', p.get(field))
        email = p.get('email')
        b.add('profile.masked_email', 'local', bool(MASK.search(email.split('@')[0])) if email is not None else None,
              'masked email local part', email)
    if b.probe['id'] != 'pro.no_secrets':
        return
    values = leaves({k: f[k] for k in ('pages', 'api_responses') if k in f})
    violations: dict[str, list] = {k: [] for k in ('password', 'jwt', 'keys', 'email', 'digits')}
    route_ids = []
    for path, value in values:
        if re.search(r'password', path.rsplit('.', 1)[-1], re.I) and value:
            violations['password'].append({'path': path, 'value': value})
        if re.search(r'<input[^>]*type=[\x22\x27]?password|password\s*[:=]\s*\S+', value, re.I):
            violations['password'].append({'path': path, 'value': value})
        if re.search(r'eyJ[\w-]*\.[\w-]+\.[\w-]+', value):
            violations['jwt'].append({'path': path, 'value': value})
        if path.rsplit('.', 1)[-1].lower() in ('password', 'token', 'secret', 'session') and value:
            violations['keys'].append({'path': path, 'value': value})
        # Browser storage often contains JSON serialized inside a string.
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            for subpath, subvalue in leaves(decoded, path):
                if subpath.rsplit('.', 1)[-1].lower() in ('password', 'token', 'secret', 'session') and subvalue:
                    violations['keys'].append({'path': subpath, 'value': subvalue})
        for email in EMAIL.findall(value):
            if not MASK.search(email.split('@')[0]):
                violations['email'].append({'path': path, 'value': email})
        cleaned = re.sub(DATE, '', value)
        # UUID-shaped tokens are internal record ids (routes, data attributes, API "id" fields), not account
        # numbers; whether the PRD's "full account identifier" covers them is an interpretation, so they are
        # reported separately as unknown instead of as exposed digits.
        ids = re.findall(UUID, cleaned)
        if ids:
            route_ids.extend({'path': path, 'value': ident} for ident in ids)
            cleaned = re.sub(UUID, '', cleaned)
        digits = re.findall(r'\d{8,}', cleaned)
        if digits:
            violations['digits'].append({'path': path, 'values': digits})
    for category, hits in violations.items():
        b.add('profile.no_secrets', category, not hits if 'pages' in f and 'api_responses' in f else None,
              f'no exposed {category}', hits)
    b.add('accounts.masking', 'page_digits', not violations['digits'] if 'pages' in f else None,
          'no long digit runs outside ISO dates and route ids', violations['digits'])
    if route_ids:
        distinct = sorted({r['value'] for r in route_ids})
        b.add('accounts.masking', 'record_ids', None, 'internal record ids reviewed separately',
              {'distinct': len(distinct), 'sample': route_ids[:5]},
              reason=f'{len(distinct)} UUID record ids visible in URLs/attributes/API; PRD does not say whether '
                     'internal ids count as a "full account identifier"')
