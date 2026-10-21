"""Typed, cached decisions with bounded retries."""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from email.utils import parsedate_to_datetime
from datetime import datetime, UTC

import httpx

from ..models import CostEntry


class DecisionAnswers(dict):
    def __init__(self, answers, model):
        super().__init__(answers)
        self.model = model


class DecisionUnavailable(RuntimeError):
    pass


def validate_answers(answers, questions):
    if not isinstance(answers, dict):
        raise ValueError('missing answers')
    def probability(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            raise ValueError('answer type mismatch')
        if question['type'] == 'noul':
            if not probability(answer.get('noul')):
                raise ValueError('invalid noul')
        elif question['type'] == 'choice':
            options = question['criteria']
            probabilities = answer.get('probabilities')
            if (answer.get('choice') not in options or not probability(answer.get('confidence'))
                    or not isinstance(probabilities, dict) or answer['choice'] not in probabilities
                    or any(k not in options or not probability(v) for k, v in probabilities.items())):
                raise ValueError('invalid choice')
            # Thresholds read the distribution, so it must be a distribution and agree with the chosen option;
            # otherwise "unsure" with P(violates)=.95 would keep a failure.
            if (abs(sum(probabilities.values()) - 1) > .05
                    or probabilities[answer['choice']] < max(probabilities.values()) - 1e-6):
                raise ValueError('inconsistent choice distribution')
        else:
            raise ValueError('unsupported question')
    return answers


class DecisionClient:
    provider = 'typesafe'

    def __init__(self, api_key, *, model='jev-1.13.0', meter, timeout_s=20, max_workers=8,
                 retries=3, transport=None):
        self.model, self.meter = model, meter
        self.max_workers, self.retries = max_workers, retries
        self.cache_dir = Path('.cache')
        self._key = api_key
        self._http = httpx.Client(transport=transport, timeout=timeout_s)

    def ask(self, state, questions: dict, *, phase: str, cache_dir: Path | None = None) -> dict:
        from ..verdict.link import key_for
        path = (cache_dir or self.cache_dir) / 'decide' / phase / f'{key_for(self.model, state, questions)}.json'
        try:
            cached = json.loads(path.read_text())
            answers = validate_answers(cached['answers'], questions)
            usage = cached.get('usage')  # absent in caches written before usage was stored
            self.meter.record(CostEntry(phase=phase, model=cached['model'], cache_hit=True,
                                        input_tokens=(usage or {}).get('input_tokens', 0),
                                        output_tokens=(usage or {}).get('output_tokens', 0),
                                        usage_known=usage is not None))
            return DecisionAnswers(answers, cached['model'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            delay = .5 * 2 ** attempt
            try:
                response = self._http.post('https://api.typesafe.ai/v1/systemone',
                                           headers={'Authorization': f'Bearer {self._key}'},
                                           json={'model': self.model, 'state': state, 'questions': questions})
            except httpx.TransportError as exc:  # timeouts, connection and protocol errors (e.g. server disconnect)
                failure = f'transport error: {type(exc).__name__}'
            else:
                if response.status_code in (429, 529):
                    failure = f'HTTP {response.status_code}'
                    raw = response.headers.get('Retry-After', '')
                    try:
                        wait = float(raw)
                    except ValueError:
                        try:
                            wait = (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
                        except (ValueError, TypeError):
                            wait = 0
                    delay = max(delay, min(10, max(0, wait)))
                elif not response.is_success:
                    detail = response.text.replace(self._key, '[redacted]')[:200]
                    raise DecisionUnavailable(f'HTTP {response.status_code}: {detail}') from None
                else:
                    try:
                        data = response.json()
                        answers = validate_answers(data['answers'], questions)
                        entry = CostEntry(phase=phase, model=data['model'],
                                          input_tokens=data['usage']['input_tokens'],
                                          output_tokens=data['usage']['output_tokens'],
                                          latency_ms=int((time.monotonic() - started) * 1000))
                    except (ValueError, KeyError, TypeError):
                        raise DecisionUnavailable('Malformed decision response') from None
                    self.meter.record(entry)
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps({'model': data['model'], 'answers': answers,
                                                    'usage': {'input_tokens': entry.input_tokens,
                                                              'output_tokens': entry.output_tokens}}))
                    except OSError:
                        pass
                    return DecisionAnswers(answers, data['model'])
            if attempt < self.retries:
                time.sleep(delay)
        raise DecisionUnavailable(failure) from None

    def ask_many(self, requests, *, phase, cache_dir: Path | None = None):
        def ask(request):
            try:
                return self.ask(*request, phase=phase, cache_dir=cache_dir)
            except DecisionUnavailable as exc:
                return exc
            except Exception as exc:  # noqa: BLE001 - any other failure is per-request uncertainty, not a crash
                return DecisionUnavailable(f'{type(exc).__name__}: {exc}'.replace(self._key, '[redacted]')[:200])
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(ask, requests))


class EscalationClient:
    """Preserve Gemini prompts and caches while separating escalation accounting."""
    def __init__(self, client):
        self.client, self.provider = client, client.provider

    def model_for(self, tier):
        return self.client.model_for(tier)

    def generate(self, prompt, *, phase, **kwargs):
        return self.client.generate(prompt, phase=phase + '_escalation', **kwargs)

    def record_cache_hit(self, phase, tier):
        from .meter import note_cache_hit
        note_cache_hit(self.client, phase + '_escalation', tier)


def ask_many(decider, requests, *, phase, cache_dir):
    # The cache root travels with the call; mutating the shared client would leak it into concurrent batches.
    if isinstance(decider, DecisionClient):
        return decider.ask_many(requests, phase=phase, cache_dir=cache_dir)
    return decider.ask_many(requests, phase=phase)
