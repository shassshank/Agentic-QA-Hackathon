"""Offline tests: displayed-value parsing and probe registry integrity (no browser)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from qa_agent.models import Area, ScenarioKind
from qa_agent.observe import decimal_places, local_date, parse_money
from qa_agent.plan import select_probes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$4,369.75USD", Decimal("4369.75")),
        ("Money out: −$120.00", Decimal("-120.00")),
        ("Money in: +$950.00", Decimal("950.00")),
        ("Everyday Checking (•••• 1042) — $1,234,567.89", Decimal("1234567.89")),
        ("-5", Decimal("-5")),
        ("no money here", None),
        (None, None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$25.00 (•••• 1042)", Decimal("25.00")),
        ("$25.00 as of 2026-10-20", Decimal("25.00")),
        ("Oct 20, 2026, 10:00 AM $3.00", Decimal("3.00")),
        ("USD 12.50", Decimal("12.50")),
    ],
)
def test_parse_money_ignores_identifiers_dates_and_times(text, expected):
    assert parse_money(text) == expected


def _snap(balances: dict[str, str | None], rows: list[str]) -> dict:
    return {"accounts": [{"name": n, "identifier_text": f"•••• {n}", "balance": b} for n, b in balances.items()],
            "rows": [{"id": r, "cells": {"Description": r}} for r in rows]}


def _ctx():
    from qa_agent.plan.registry import ProbeContext

    return ProbeContext(probe=None, harness=None, run_tag="abc123")  # type: ignore[arg-type]


def test_restore_refuses_when_foreign_activity_in_window():
    from qa_agent.plan.flows import restore

    before = _snap({"1042": "100.00", "7781": "50.00"}, ["old"])
    after = _snap({"1042": "49.00", "7781": "101.00"}, ["old", "qa-abc123-valid", "someone else's transfer"])
    record = restore(_ctx(), before, after)
    assert record["needed"] and record["restored"] is False and "foreign" in record["reason"]
    assert record["transfers"] == []


def test_restore_refuses_when_a_balance_is_unreadable():
    from qa_agent.plan.flows import restore

    before = _snap({"1042": "100.00", "7781": "50.00"}, [])
    record = restore(_ctx(), before, _snap({"1042": "100.00", "7781": None}, []))
    assert record["restored"] is False and "unreadable" in record["reason"]


def test_window_delta_allows_our_memo_less_rows():
    from qa_agent.plan.flows import window_delta

    before = _snap({"1042": "100.00", "7781": "50.00"}, ["old"])
    after = _snap({"1042": "98.00", "7781": "52.00"}, ["old", "Transfer from A to B"])
    window = window_delta(before, after, run_tag="abc123", untagged_allowed=1)
    assert window["foreign_rows"] == [] and window["delta"] == {"•••• 1042": "-2.00", "•••• 7781": "2.00"}


def test_decimal_places_reads_last_token():
    assert decimal_places("$8,275.00USD") == 2
    assert decimal_places("$8,275.5") == 1
    assert decimal_places("$8,275") == 0
    assert decimal_places("") is None


def test_local_date_uses_target_timezone():
    # 2025-01-03 06:30 UTC is still 2025-01-02 in Los Angeles.
    assert local_date("2025-01-03T06:30:00Z", "America/Los_Angeles") == date(2025, 1, 2)
    assert local_date("2025-01-03T06:30:00Z", "UTC") == date(2025, 1, 3)
    assert local_date("not a date", "UTC") is None


def test_registry_ids_unique_and_every_area_has_scenarios():
    probes = select_probes()
    assert len({p.id for p in probes}) == len(probes)
    kinds: dict[Area, set[ScenarioKind]] = defaultdict(set)
    for p in probes:
        assert p.id.split(".")[0].upper()[:3] == p.area.value[:3]
        assert p.behaviours, p.id
        kinds[p.area].add(p.kind)
    for area in (Area.SES, Area.ACC, Area.TRF, Area.HIS, Area.PRO, Area.GEN):
        assert kinds[area] >= {ScenarioKind.NORMAL, ScenarioKind.NEGATIVE} or kinds[area] >= {
            ScenarioKind.NORMAL, ScenarioKind.EDGE}, (area, kinds[area])
    # Every mutating probe must restore balances; they all go through the shared transfer case.
    assert all(p.area == Area.TRF for p in probes if p.mutating)


def test_select_by_prefix_and_skip_mutating():
    assert {p.id for p in select_probes(["trf.reject"])} == {
        f"trf.reject.{c}" for c in ("blank", "whitespace", "zero", "zero_decimal", "negative", "non_numeric",
                                    "over_precision")}
    assert not any(p.mutating for p in select_probes(skip_mutating=True))
