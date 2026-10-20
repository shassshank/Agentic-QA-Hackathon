"""Token and cost accounting for every LLM call in a run."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..models import CostEntry

# USD per 1M tokens: (input, output). Thinking tokens bill as output. Unknown models are reported
# as unpriced rather than guessed; fill in from the provider's current price page.
PriceTable = dict[str, tuple[float, float]]


class CostMeter:
    def __init__(self, prices: PriceTable | None = None) -> None:
        self.entries: list[CostEntry] = []
        self.prices: PriceTable = prices or {}

    def record(self, entry: CostEntry) -> None:
        self.entries.append(entry)

    def cost_of(self, entry: CostEntry) -> float | None:
        price = self.prices.get(entry.model)
        if price is None:
            return None
        input_price, output_price = price
        billable_input = max(entry.input_tokens - entry.cached_tokens, 0)
        return (billable_input * input_price + (entry.output_tokens + entry.thinking_tokens) * output_price) / 1e6

    def by_phase(self) -> dict[str, dict[str, float | int | None]]:
        summary: dict[str, dict[str, float | int | None]] = defaultdict(
            lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cost_usd": 0.0}
        )
        for entry in self.entries:
            row = summary[entry.phase]
            row["calls"] = int(row["calls"] or 0) + (0 if entry.cache_hit else 1)
            row["input_tokens"] = int(row["input_tokens"] or 0) + entry.input_tokens
            row["output_tokens"] = int(row["output_tokens"] or 0) + entry.output_tokens
            row["thinking_tokens"] = int(row["thinking_tokens"] or 0) + entry.thinking_tokens
            cost = self.cost_of(entry)
            row["cost_usd"] = None if cost is None or row["cost_usd"] is None else float(row["cost_usd"]) + cost
        return dict(summary)

    def total_cost(self) -> float | None:
        costs = [self.cost_of(e) for e in self.entries if not e.cache_hit]
        return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": [e.model_dump() for e in self.entries],
            "by_phase": self.by_phase(),
            "total_cost_usd": self.total_cost(),
            "prices_per_million": self.prices,
        }
        path.write_text(json.dumps(payload, indent=2))
