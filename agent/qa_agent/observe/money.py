"""Money and date parsing for displayed values. Never converts money to float."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

# Sign may be ASCII "-", Unicode minus "−" or "+"; currency symbol and thousands separators optional.
_MONEY = re.compile(r"(?P<sign>[+\-−])?\s*(?:[A-Z]{3}\s*)?[$€£]?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?")


def parse_money(text: str | None) -> Decimal | None:
    """Last money-looking token in the text, signed. '−$1,250.00' → Decimal('-1250.00')."""
    if not text:
        return None
    matches = list(_MONEY.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    raw = m.group("num").replace(",", "") + (f".{m.group('frac')}" if m.group("frac") is not None else "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return -value if m.group("sign") in ("-", "−") else value


def decimal_places(text: str | None) -> int | None:
    """Digits after the decimal point in the last money token (None when no token)."""
    if not text:
        return None
    matches = list(_MONEY.finditer(text))
    if not matches:
        return None
    frac = matches[-1].group("frac")
    return len(frac) if frac is not None else 0


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
