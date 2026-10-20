"""Money and date parsing for displayed values. Never converts money to float."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

# Sign may be ASCII "-", Unicode minus "−" or "+"; currency marker before or after; separators optional.
_MONEY = re.compile(
    r"(?<![\w.])(?P<sign>[+\-−])?\s*(?P<cur>[$€£]|\b[A-Z]{3}\s)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?(?P<cur2>\s?(?:[A-Z]{3})\b)?"
)
# Numbers that are not amounts: dates, times and (masked) identifiers are blanked out before matching.
_NOT_MONEY = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}(?:T[\d:.]+Z?)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}:\d{2}(?::\d{2})?"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|(?:[•●·*xX]{2,}|\bending(?: in)?|#|\bno\.)\s?-?\d+",
)


def _best_token(text: str) -> re.Match[str] | None:
    """Most money-like token: currency-marked beats decimal beats bare integer; last one within a tier."""
    cleaned = _NOT_MONEY.sub(lambda m: " " * len(m.group(0)), text)
    tiers: dict[int, re.Match[str]] = {}
    for m in _MONEY.finditer(cleaned):
        tier = 2 if (m.group("cur") or m.group("cur2")) else 1 if m.group("frac") is not None else 0
        tiers[tier] = m
    return tiers[max(tiers)] if tiers else None


def parse_money(text: str | None) -> Decimal | None:
    """Signed amount from displayed text. '−$1,250.00' → Decimal('-1250.00'); ids and dates are ignored."""
    if not text:
        return None
    m = _best_token(text)
    if m is None:
        return None
    raw = m.group("num").replace(",", "") + (f".{m.group('frac')}" if m.group("frac") is not None else "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return -value if m.group("sign") in ("-", "−") else value


def decimal_places(text: str | None) -> int | None:
    """Digits after the decimal point in the amount parse_money would pick (None when no amount)."""
    if not text:
        return None
    m = _best_token(text)
    if m is None:
        return None
    return len(m.group("frac")) if m.group("frac") is not None else 0


def local_date(iso: str | None, tz: str) -> date | None:
    """Calendar date of an ISO-8601 instant in the given timezone."""
    if not iso:
        return None
    try:
        instant = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        return instant.date()
    return instant.astimezone(ZoneInfo(tz)).date()
