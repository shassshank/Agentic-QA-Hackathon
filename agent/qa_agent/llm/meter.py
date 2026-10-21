"""Token and cost accounting for every LLM call in a run."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..models import CostEntry

# USD per 1M tokens: (input, output). Thinking tokens bill as output. Unknown models are reported
# as unpriced rather than guessed; fill in from the provider's current price page.
PriceTable = dict[str, tuple[float, float]]

# Paid-tier list prices, checked 2026-10-21 (ai.google.dev/gemini-api/docs/pricing; TypeSafe console).
# gemini-3.8-flash doubles to (1.50, 7.50) from 2027-01-01. gemini-3.7-flash and gemini-3.5-flash are
# not on the price page, so a run that falls back to them reports an unpriced lower bound.
DEFAULT_PRICES: PriceTable = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "jev-1.13.0": (0.042, 0.0),
    "jev-1.14.0": (0.042, 0.0),
}


def note_cache_hit(client, phase: str, tier: str) -> None:
    """Record a decision served from a cache that does not store usage (clients without metering ignore it)."""
    record = getattr(client, "record_cache_hit", None)
    if record is not None:
        record(phase, tier)


class CostMeter:
    def __init__(self, prices: PriceTable | None = None) -> None:
        self.entries: list[CostEntry] = []
        self.prices: PriceTable = {**DEFAULT_PRICES, **(prices or {})}

    def record(self, entry: CostEntry) -> None:
        self.entries.append(entry)

    def cost_of(self, entry: CostEntry) -> float | None:
        """Price of the call itself, whether or not this run served it from cache; None when unknown.

        Provider-side cached input tokens are charged at the full input rate: their discounted rate is not in
        the price table, so this is an upper bound for those tokens rather than pricing them as free.
        """
        price = self.prices.get(entry.model)
        if price is None or not entry.usage_known:
            return None
        input_price, output_price = price
        return (entry.input_tokens * input_price + (entry.output_tokens + entry.thinking_tokens) * output_price) / 1e6

    def by_phase(self) -> dict[str, dict[str, float | int | None]]:
        """Billed cost covers calls made in this run; cold cost also prices answers served from cache.

        Costs are sums of the entries whose cost is known; ``unpriced_calls`` and ``missing_usage`` count the
        rest, so one unknown entry never erases the known spend and never goes unflagged.
        """
        summary: dict[str, dict[str, float | int | None]] = defaultdict(
            lambda: {"calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
                     "cost_usd": 0.0, "cold_cost_usd": 0.0, "unpriced_calls": 0, "missing_usage": 0}
        )
        for entry in self.entries:
            row = summary[entry.phase]
            counter = "cached_calls" if entry.cache_hit else "calls"
            row[counter] = int(row[counter] or 0) + 1
            row["input_tokens"] = int(row["input_tokens"] or 0) + entry.input_tokens
            row["output_tokens"] = int(row["output_tokens"] or 0) + entry.output_tokens
            row["thinking_tokens"] = int(row["thinking_tokens"] or 0) + entry.thinking_tokens
            cost = self.cost_of(entry)
            if cost is None:
                flag = "missing_usage" if not entry.usage_known else "unpriced_calls"
                row[flag] = int(row[flag] or 0) + 1
                continue
            row["cold_cost_usd"] = float(row["cold_cost_usd"] or 0) + cost
            if not entry.cache_hit:
                row["cost_usd"] = float(row["cost_usd"] or 0) + cost
        return dict(summary)

    def total_cost(self, *, cold: bool = False) -> float:
        """Sum of known costs; see ``complete`` for whether anything was left out."""
        return sum(c for e in self.entries if cold or not e.cache_hit if (c := self.cost_of(e)) is not None)

    def complete(self, *, cold: bool = False) -> bool:
        return all(self.cost_of(e) is not None for e in self.entries if cold or not e.cache_hit)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": [e.model_dump() for e in self.entries],
            "by_phase": self.by_phase(),
            "total_cost_usd": self.total_cost(),
            "cold_cost_usd": self.total_cost(cold=True),
            "complete": self.complete(),
            "cold_complete": self.complete(cold=True),
            "prices_per_million": self.prices,
        }
        path.write_text(json.dumps(payload, indent=2))
