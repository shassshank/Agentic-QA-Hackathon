"""Shared comparisons keep missing data distinct from observed violations."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Check, Evidence

SUCCESS = re.compile(r'complete|success|transferred|done', re.I)
BUSY = re.compile(r'submitting|loading|processing|…|\.\.\.', re.I)


def short(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:700]


def number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def key(row: dict) -> str:
    return row.get('id') or json.dumps(tuple(row.get('cells', {}).items()), ensure_ascii=False)


def keys(rows: list[dict]) -> set[str]:
    return {key(r) for r in rows}


def account_key(account: dict) -> str:
    return account.get('identifier_text') or account.get('name', '')


def balances(block: dict) -> dict[str, Decimal | None]:
    return {account_key(a): number(a.get('balance')) for a in block.get('accounts', [])}


def records(value: Any, name: str) -> list[dict] | None:
    if isinstance(value, list):
        return value
    return value.get(name) if isinstance(value, dict) else None


def text(value: Any) -> str:
    if isinstance(value, dict):
        return ' '.join(text(v) for v in value.values())
    if isinstance(value, list):
        return ' '.join(text(v) for v in value)
    return str(value or '')


def success_text(message: str) -> bool:
    return bool(SUCCESS.search(message)) and not bool(re.search(
        r'not (?:complete|success|transferred|done)|unsuccess|fail|error|declin|reject', message, re.I))


def success(feedback: dict) -> bool:
    messages = feedback.get('statuses', []) + feedback.get('alerts', [])
    errors = bool(feedback.get('field_errors')) or any(not success_text(a) for a in feedback.get('alerts', []))
    return any(success_text(m) for m in messages) and not errors


def content(state: dict) -> bool | None:
    path = state.get('requested', state.get('path', '')).split('?')[0]
    field = ('account_cards' if path == '/dashboard' else 'transfer_form' if path == '/transfer'
             else 'profile_fields' if path == '/profile' else 'transaction_rows')
    if field not in state:
        return None
    # A detail/history page can legitimately have an empty history.
    return bool(state[field]) or (field == 'transaction_rows' and bool(re.search(r'no transactions|no results',
                                                                                 state.get('text', ''), re.I)))


def protected(state: dict) -> bool | None:
    fields = ('account_cards', 'transaction_rows', 'transfer_form', 'profile_fields', 'header_customer')
    if any(state.get(k) for k in fields):
        return True
    return False if all(k in state for k in fields) else None


class Builder:
    def __init__(self, probe: dict):
        self.probe = probe
        self.facts = probe.get('facts', {})
        self.checks: list[Check] = []
        self.evidence = Evidence.model_validate(probe.get('_evidence', {}))
        shots: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for k, v in value.items():
                    if k == 'screenshot' and isinstance(v, str):
                        shots.append(v.rsplit('/', 1)[-1])
                    else:
                        collect(v)
            elif isinstance(value, list):
                for v in value:
                    collect(v)
        collect(self.facts)
        self.evidence.screenshots = sorted(set(self.evidence.screenshots + shots))
        restore = self.facts.get('restore', {})
        self.foreign = ('foreign' in restore.get('reason', '').lower()
                        or bool(restore.get('window', {}).get('foreign_rows')))

    def add(self, behaviour: str, suffix: str, condition: bool | None, expected: str, observed: Any,
            *, reason: str | None = None, sensitive: bool = False, judge: Any = None, area: str | None = None) -> None:
        status = 'unknown' if condition is None else 'pass' if condition else 'fail'
        if judge is not None:
            status, reason = 'unknown', reason or 'wording quality requires judge'
        if (sensitive or behaviour.startswith('history.')) and self.foreign:
            status, reason = 'unknown', 'concurrent activity'
        if self.probe.get('status') != 'completed':
            status, reason = 'unknown', f"probe {self.probe.get('status')}: {self.probe.get('error') or 'incomplete'}".splitlines()[0]
        self.checks.append(Check(
            id=f"{behaviour}.{suffix}", behaviour=behaviour, area=area or self.probe.get('area', ''),
            title=f'{behaviour}: {suffix}', status=status,
            reason=None if status == 'pass' else (reason or ('required observation missing' if condition is None
                                                          else f'expected {expected}; observed {short(observed)}')),
            expected=expected, observed=short(observed), probe_id=self.probe['id'], evidence=self.evidence,
            needs_judge=judge is not None, judge_input=None if judge is None else {
                'text': judge, 'context': f"{self.probe['id']}: {behaviour}; {expected}"},
        ))

    def judge(self, behaviour: str, suffix: str, visible: Any, context: str) -> None:
        self.add(behaviour, suffix, None, context, visible, judge=visible)
