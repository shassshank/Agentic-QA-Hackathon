"""Bound recovery so it cannot repeat a mutation or hide an access test."""

from __future__ import annotations


class RecoveryPolicy:
    def __init__(self, probe_id: str | None = None) -> None:
        # Off unless a probe is named: an ad-hoc ScenarioRun must observe the app exactly as it behaves.
        self.enabled = probe_id is not None and not (
            probe_id.startswith('ses.') or probe_id in {'gen.loading', 'gen.recoverable_error'})
        self.used: set[tuple[str, str]] = set()

    def allow(self, kind: str, action: str, target: str) -> bool:
        key = (kind, '' if kind == 'session' else target)
        if not self.enabled or action == 'submit' or key in self.used:
            return False
        self.used.add(key)
        return True

    def retry_step(self, action: str, reason: str, attempt: int) -> bool:
        return self.enabled and action != 'submit' and attempt < 2 and any(
            word in reason.lower() for word in ('detached', 'animating', 'not stable'))
