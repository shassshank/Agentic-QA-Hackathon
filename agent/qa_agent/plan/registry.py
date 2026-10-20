"""Probe registry: deterministic scenarios that drive the UI and record structured facts.

A probe never decides pass/fail. It records what it did and what it saw (facts + evidence bundle);
oracles in the verify layer turn facts into checks, and the verdict layer maps checks to requirements.
"""

from __future__ import annotations

import json
import secrets
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ..execute import ActionError, Harness, ScenarioRun
from ..execute.browser import DESKTOP
from ..models import Area, ScenarioKind

ProbeStatus = Literal["completed", "blocked", "errored"]


@dataclass(frozen=True)
class Probe:
    id: str
    area: Area
    kind: ScenarioKind
    title: str
    behaviours: tuple[str, ...]
    fn: Callable[[ProbeContext], None]
    mutating: bool = False
    viewport: dict[str, int] | None = None


class ProbeResult(BaseModel):
    id: str
    area: Area
    kind: ScenarioKind
    title: str
    behaviours: list[str]
    mutating: bool
    status: ProbeStatus
    facts: dict[str, Any]
    error: str | None = None
    evidence_dir: str
    sessions: list[str]
    started_at: datetime
    duration_ms: int


REGISTRY: dict[str, Probe] = {}


def probe(id: str, area: Area, kind: ScenarioKind, title: str, *, behaviours: list[str],
          mutating: bool = False, viewport: dict[str, int] | None = None) -> Callable:
    def register(fn: Callable[[ProbeContext], None]) -> Callable[[ProbeContext], None]:
        if id in REGISTRY:
            raise ValueError(f"duplicate probe id {id}")
        REGISTRY[id] = Probe(id, area, kind, title, tuple(behaviours), fn, mutating, viewport)
        return fn
    return register


class ProbeBlocked(RuntimeError):
    """The probe could not reach the state it needs (e.g. no second account); report unverified."""


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass
class ProbeContext:
    probe: Probe
    harness: Harness
    run_tag: str
    facts: dict[str, Any] = field(default_factory=dict)
    sessions: list[str] = field(default_factory=list)
    _run: ScenarioRun | None = None

    @property
    def run(self) -> ScenarioRun:
        assert self._run is not None, "no active session"
        return self._run

    def fact(self, key: str, value: Any) -> Any:
        self.facts[key] = jsonable(value)
        return value

    def append(self, key: str, value: Any) -> Any:
        self.facts.setdefault(key, []).append(jsonable(value))
        return value

    def memo(self, label: str) -> str:
        """Unique, recognisable memo so our records are distinguishable from anyone else's."""
        return f"qa-{self.run_tag}-{label}"[:60]

    @contextmanager
    def session(self, label: str = "main", *, viewport: dict[str, int] | None = None) -> Iterator[ScenarioRun]:
        """A fresh browser context (no cookies, no storage). Nested use keeps the outer one intact."""
        name = self.probe.id if label == "main" else f"{self.probe.id}--{label}"
        self.sessions.append(name)
        outer = self._run
        with self.harness.scenario(name, viewport=viewport or self.probe.viewport or DESKTOP) as run:
            self._run = run
            try:
                yield run
            finally:
                self.facts.setdefault("_timeline", {})[label] = run.timeline()
                self._run = outer


def run_probe(probe_def: Probe, harness: Harness, run_tag: str) -> ProbeResult:
    ctx = ProbeContext(probe_def, harness, run_tag)
    started = datetime.now(UTC)
    t0 = time.monotonic()
    status: ProbeStatus = "completed"
    error = None
    try:
        with ctx.session():
            probe_def.fn(ctx)
    except ProbeBlocked as exc:
        status, error = "blocked", str(exc)
    except ActionError as exc:
        status, error = "blocked", str(exc)
    except Exception as exc:  # noqa: BLE001 - one broken probe must not stop the run
        status, error = "errored", f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc(limit=4)}"
    return ProbeResult(
        id=probe_def.id, area=probe_def.area, kind=probe_def.kind, title=probe_def.title,
        behaviours=list(probe_def.behaviours), mutating=probe_def.mutating, status=status,
        facts=ctx.facts, error=error, evidence_dir=str(harness.out_dir / probe_def.id), sessions=ctx.sessions,
        started_at=started, duration_ms=int((time.monotonic() - t0) * 1000),
    )


def select_probes(only: list[str] | None = None, skip_mutating: bool = False) -> list[Probe]:
    from . import probes  # noqa: F401 - registers probes on import

    chosen = [p for p in REGISTRY.values() if not only or any(p.id == o or p.id.startswith(o + ".") for o in only)]
    return [p for p in chosen if not (skip_mutating and p.mutating)]


def run_probes(base_url: str, out_dir: Path, *, only: list[str] | None = None, skip_mutating: bool = False,
               headless: bool = True, on_result: Callable[[ProbeResult], None] | None = None,
               run_tag: str | None = None) -> list[ProbeResult]:
    tag = run_tag or secrets.token_hex(3)
    results: list[ProbeResult] = []
    chosen = select_probes(only, skip_mutating)
    path = out_dir / "observations.json"

    def write(complete: bool) -> None:
        # Rewritten after every probe (atomically) so an interrupted run keeps what it observed.
        payload = {"run_tag": tag, "base_url": base_url, "complete": complete, "planned": [p.id for p in chosen],
                   "results": [r.model_dump(mode="json") for r in results]}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        tmp.replace(path)

    with Harness(base_url, out_dir / "evidence", headless=headless) as harness:
        for probe_def in chosen:
            result = run_probe(probe_def, harness, tag)
            results.append(result)
            write(complete=False)
            if on_result:
                on_result(result)
    write(complete=True)
    return results
