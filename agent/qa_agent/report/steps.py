"""Recorded steps in plain words: the report shows what a person would do, not the locator patterns used."""
from __future__ import annotations

import re

ROLE_NOUN = {'button': 'button', 'link': 'link', 'tab': 'tab', 'menuitem': 'menu item', 'checkbox': 'checkbox'}


def humanize_pattern(pattern: str) -> str:
    """'^\\s*(from|source)\\b' → 'From'; 'link:^\\s*View\\ all\\ transactions\\s*$' → 'View all transactions'.

    Locators are case-insensitive regexes over accessible names; the first alternative is the name a reader
    expects, optional groups are dropped and escapes removed."""
    text = pattern.replace('\\s*', ' ').replace('\\s+', ' ').replace('\\b', '').replace('^', '').replace('$', '')
    text = re.sub(r'\([^()]*\)\?', '', text)                       # optional groups: '(transaction )?'
    text = re.sub(r'\(([^()|]*)\|[^()]*\)', r'\1', text)            # alternation groups: first alternative
    text = text.split('|')[0]                                      # top-level alternation
    text = re.sub(r'\\(.)', r'\1', text)                            # escaped characters
    text = re.sub(r'[.*+?\[\]{}()]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:1].upper() + text[1:] if text else pattern


def _control(target: str) -> tuple[str, str]:
    role, sep, name = target.partition(':')
    if sep and role in ROLE_NOUN:
        return humanize_pattern(name), ROLE_NOUN[role]
    return humanize_pattern(target), 'field'


def describe(action: str, target: str | None, value: str | None) -> str:
    target, value = target or '', value or ''
    if action == 'goto':
        return f'Open {target or "/"}'
    if action == 'enter_demo':
        return f'Enter the demo (lands on {target})' if target else 'Enter the demo'
    if action == 'click':
        name, noun = _control(target)
        return f'Click the "{name}" {noun}'
    if action == 'fill':
        name, _ = _control(target)
        return f'Type "{value}" into the {name} field' if value.strip() else f'Leave the {name} field as "{value}"'
    if action == 'select':
        name, _ = _control(target)
        return f'Choose "{value.strip()}" in the {name} field'
    if action == 'submit':
        name, _ = _control(target)
        clicks = re.match(r'clicks=(\d+)', value)
        return f'Click the "{name}" button {clicks.group(1)} times rapidly' if clicks else f'Click the "{name}" button'
    if action == 'throttle':
        ms = re.match(r'(\d+)ms', value)
        if ms and int(ms.group(1)) == 0:
            return 'Restore normal network speed'
        return f'Slow every network request by {ms.group(1)} ms' if ms else 'Change network speed'
    if action == 'reload':
        return 'Reload the page'
    words = ' '.join(p for p in (action.replace('_', ' '), target, value) if p)
    return words[:1].upper() + words[1:]


def describe_recorded(step: str) -> str:
    """Steps stored on failures are 'action target value' strings; split on the known action word."""
    action, _, rest = step.partition(' ')
    if action in ('goto', 'enter_demo', 'reload'):
        return describe(action, rest or None, None)
    if action == 'throttle':
        return describe(action, None, rest)
    if action == 'click':  # no value: the accessible name may contain plain spaces ('button:enter demo')
        return describe(action, rest, None)
    if action == 'submit':
        target, clicks = re.match(r'(.*?)(?:\s+(clicks=\d+))?$', rest).groups()
        return describe(action, target, clicks)
    if action in ('fill', 'select'):
        # Patterns escape their spaces ('View\ all'), so the target ends at the first unescaped space.
        target, value = re.match(r'((?:\\.|\S)*)\s?(.*)', rest, re.S).groups()
        return describe(action, target, value)
    return describe(action, rest, None)
