"""Resolve changed controls conservatively from a fresh interactive snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient

SNAPSHOT_JS = r"""() => [...document.querySelectorAll('button,a[href],input,select,textarea,[role]')]
.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
.map((e, index) => {
  e.setAttribute('data-qa-heal', String(index));
  const label = [...(e.labels || [])].map(l => l.textContent.trim()).join(' ');
  const refs = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
  return {index, role: e.getAttribute('role') || ({BUTTON:'button', A:'link', SELECT:'combobox',
    TEXTAREA:'textbox', INPUT:e.type === 'number' ? 'spinbutton' : 'textbox'}[e.tagName] || ''),
    name: e.getAttribute('aria-label') || refs || label || e.innerText || '', label,
    placeholder:e.getAttribute('placeholder') || '', id:e.id, attribute_name:e.name || '',
    text:(e.innerText || '').slice(0,200), heading:e.closest('section,main,form')?.querySelector('h1,h2,h3')?.textContent || ''};
})"""

# General language concepts, independent of any application or test fixture.
_GROUPS = ('from source origin', 'to destination recipient', 'amount sum value', 'memo note description reference',
           'apply search filter', 'clear reset', 'logout signout exit', 'transactions history activity',
           'enter start begin', 'transfer send move', 'account accounts', 'fund funds money')
_CANON = {word: group.split()[0] for group in _GROUPS for word in group.split()}


def tokens(value: str) -> set[str]:
    value = re.sub(r'log\s*out', 'logout', value.casefold())
    value = re.sub(r'sign\s*out', 'signout', value)
    return {_CANON.get(w, w) for w in re.findall(r'[a-z]+', value)
            if w not in {'the', 'a', 'an', 'usd', 'b', 's', 'i'}}


def match(wanted: str, role: str | None, candidates: list[dict]) -> tuple[int, float] | None:
    words = tokens(wanted)
    scored = []
    for c in candidates:
        if role and c['role'] != role:
            continue
        other = tokens(c['name'] + ' ' + c.get('label', ''))
        score = len(words & other) / len(words | other) if words | other else 0
        scored.append((c['index'], score))
    scored.sort(key=lambda item: item[1], reverse=True)
    if not scored or scored[0][1] < .45 or (len(scored) > 1 and scored[0][1] == scored[1][1]):
        return None
    return scored[0]


class Decision(BaseModel):
    index: int | None
    confidence: float = Field(ge=0, le=1)
    reason: str


class Healer:
    def __init__(self, cache_dir: Path, client: LLMClient | None = None) -> None:
        self.cache_dir, self.client = cache_dir, client
        self.names: dict[str, str] = {}

    def choose(self, intent: str, wanted: str, role: str | None, candidates: list[dict]):
        eligible = [c for c in candidates if not role or c['role'] == role]
        remembered = [c for c in eligible if c['name'] == self.names.get(intent)]
        result = (remembered[0]['index'], 1.0) if len(remembered) == 1 else match(wanted, role, candidates)
        method = 'deterministic'
        if result is None and self.client:
            prompt = ('Resolve only the intended UI control. Abstain if ambiguous. Return index=null when uncertain.\n'
                      + json.dumps({'intent': intent, 'candidates': eligible}, sort_keys=True))
            digest = hashlib.sha256((self.client.model_for('fast') + prompt).encode()).hexdigest()
            path = self.cache_dir / f'{digest}.json'
            try:
                if path.exists():
                    decision = Decision.model_validate_json(path.read_text())
                else:
                    response = self.client.generate(prompt, phase='heal', tier='fast', schema=Decision)
                    decision = Decision.model_validate(response.parsed)
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(decision.model_dump_json())
                if decision.index is not None and decision.confidence >= .85:
                    result = decision.index, decision.confidence
                    method = 'llm'
            except Exception:  # provider/cache failure leaves the action blocked
                return None
        if result is None:
            return None
        candidate = next((c for c in eligible if c['index'] == result[0]), None)
        if candidate is None:
            return None
        self.names[intent] = candidate['name']
        return candidate, method, result[1]
