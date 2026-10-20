"""Reusable UI flows for probes: discovery, state snapshots, transfer attempts and net-zero restore.

Controls are located by label patterns rather than exact strings so reworded labels ("Source account",
"Destination") still resolve; exact names are only the first preference.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..execute import ScenarioRun
from ..observe import AccountView, read_account_detail, read_accounts, read_feedback, read_transactions
from ..observe import read_transfer_balances
from .registry import ProbeBlocked, ProbeContext, jsonable

FROM = re.compile(r"^\s*(from|source)\b", re.I)
TO = re.compile(r"^\s*(to|destination)\b", re.I)
AMOUNT = re.compile(r"amount", re.I)
MEMO = re.compile(r"memo|note|reference", re.I)
SUBMIT = re.compile(r"transfer|submit|send|confirm", re.I)

NAV = {
    "dashboard": (re.compile(r"dashboard|home|overview", re.I), "/dashboard"),
    "transactions": (re.compile(r"transactions|history|activity", re.I), "/transactions"),
    "transfer": (re.compile(r"transfer|move money", re.I), "/transfer"),
    "profile": (re.compile(r"profile|account settings|me\b", re.I), "/profile"),
}


def nav_path(run: ScenarioRun, key: str) -> str:
    """Path of a primary navigation link, discovered from the nav landmark (cached per session)."""
    cache: dict[str, str] = run.__dict__.setdefault("_nav_cache", {})
    if key in cache:
        return cache[key]
    pattern, default = NAV[key]
    path = default
    try:
        link = run.page.get_by_role("navigation").get_by_role("link", name=pattern).first
        if link.count():
            href = link.get_attribute("href", timeout=2000)
            if href and href.startswith("/"):
                path = href
    except PlaywrightError:
        pass
    cache[key] = path
    return path


def now_ms() -> int:
    return int(time.time() * 1000)


def last_api_json(run: ScenarioRun, method: str, path_suffix: str) -> Any:
    """Most recent captured API response whose path ends with the suffix (query ignored)."""
    for record in reversed(run.recorder.network):
        path = record.url.split("?")[0].rstrip("/")
        if record.method == method and path.endswith(path_suffix):
            return record.response_body
    return None


def accounts_pair(accounts: list[AccountView]) -> tuple[AccountView, AccountView]:
    """Source = checking-like account, destination = savings-like; otherwise the first two."""
    if len(accounts) < 2:
        raise ProbeBlocked(f"need two accounts for a transfer, found {len(accounts)}")
    checking = next((a for a in accounts if re.search(r"check|current", a.text, re.I)), accounts[0])
    savings = next((a for a in accounts if a is not checking and re.search(r"sav", a.text, re.I)),
                   next(a for a in accounts if a is not checking))
    return checking, savings


def dashboard_accounts(run: ScenarioRun) -> list[AccountView]:
    run.goto(nav_path(run, "dashboard"))
    accounts = read_accounts(run.page)
    if not accounts:
        raise ProbeBlocked("no accounts visible on the dashboard")
    return accounts


def snapshot(run: ScenarioRun, label: str) -> dict[str, Any]:
    """Balances (UI + API channel) and the full unfiltered history at one moment."""
    accounts = dashboard_accounts(run)
    api_accounts = last_api_json(run, "GET", "/accounts")
    run.goto(nav_path(run, "transactions"))
    table = read_transactions(run.page)
    return jsonable({
        "label": label,
        "at_ms": now_ms(),
        "accounts": accounts,
        "api_accounts": api_accounts,
        "rows": table.rows,
        "count_text": table.count_text,
        "api_transactions": last_api_json(run, "GET", "/transactions"),
    })


def balances(snap: dict[str, Any]) -> dict[str, Decimal]:
    return {a["name"]: Decimal(a["balance"]) for a in snap["accounts"] if a.get("balance") is not None}


def account_detail(run: ScenarioRun, account: AccountView | dict) -> dict[str, Any]:
    href = account.href if isinstance(account, AccountView) else account.get("href")
    if not href:
        raise ProbeBlocked("account card has no link to a detail view")
    run.goto(href)
    detail = read_account_detail(run.page, run.path())
    table = read_transactions(run.page)
    return jsonable({"detail": detail, "rows": table.rows, "empty_text": table.empty_text,
                     "error_text": table.error_text, "api_transactions": last_api_json(run, "GET", "/transactions")})


def attempt_transfer(run: ScenarioRun, *, source: str | None, destination: str | None, amount: str,
                     memo: str | None = None, clicks: int = 1, label: str = "transfer") -> dict[str, Any]:
    """Fill and submit the transfer form once; record everything visible and the API exchange."""
    run.goto(nav_path(run, "transfer"))
    if source is not None:
        run.select(FROM, option_label=source)
    if destination is not None:
        run.select(TO, option_label=destination)
    run.fill(AMOUNT, amount)
    if memo is not None:
        run.fill(MEMO, memo)
    side_before = read_transfer_balances(run.page)
    posts_before = len(run.recorder.api_calls("POST"))
    run.clear_timeline()
    submitted_at = now_ms()
    run.submit(SUBMIT, clicks=clicks)
    feedback = read_feedback(run.page)
    timeline = run.timeline()
    posts = run.recorder.api_calls("POST")[posts_before:]
    try:
        amount_after = run.page.get_by_label(AMOUNT).first.input_value(timeout=2000)
    except PlaywrightError:
        amount_after = None
    shot = run.shot(f"{label}-result")
    return jsonable({
        "input": {"source": source, "destination": destination, "amount": amount, "memo": memo, "clicks": clicks},
        "submitted_at_ms": submitted_at,
        "feedback": feedback,
        "timeline": timeline,
        "posts": [{"url": p.url, "status": p.status, "request": p.request_body, "response": p.response_body}
                  for p in posts],
        "side_balances_before": side_before,
        "side_balances_after": read_transfer_balances(run.page),
        "amount_field_after": amount_after,
        "path_after": run.path(),
        "screenshot": shot,
    })


def rows_with_memo(rows: list[dict], memo: str) -> list[dict]:
    return [r for r in rows if memo in (r.get("description") or "") or memo in " ".join(r.get("cells", {}).values())]


def restore(ctx: ProbeContext, baseline: dict[str, Any]) -> dict[str, Any]:
    """Undo any balance change relative to the baseline with compensating transfers (net-zero runs).

    Runs after every mutating or potentially-mutating probe, because a defective app may accept input
    it should reject. Records whether the original balances were reached again.
    """
    run = ctx.run
    target = balances(baseline)
    current = {a.name: a.balance for a in dashboard_accounts(run) if a.balance is not None}
    diffs = {name: current[name] - target[name] for name in target if name in current}
    record: dict[str, Any] = {"before": target, "found": current, "needed": any(diffs.values()), "transfers": []}
    if not record["needed"]:
        record["restored"] = True
        return jsonable(record)
    gainers = sorted(((n, d) for n, d in diffs.items() if d > 0), key=lambda x: -x[1])
    losers = sorted(((n, -d) for n, d in diffs.items() if d < 0), key=lambda x: -x[1])
    for gainer, gain in gainers:
        for i, (loser, loss) in enumerate(losers):
            if gain <= 0 or loss <= 0:
                continue
            amount = min(gain, loss)
            attempt = attempt_transfer(run, source=gainer, destination=loser, amount=f"{amount:.2f}",
                                       memo=ctx.memo("restore"), label="restore")
            record["transfers"].append({"from": gainer, "to": loser, "amount": f"{amount:.2f}",
                                        "posts": attempt["posts"], "feedback": attempt["feedback"]})
            gain -= amount
            losers[i] = (loser, loss - amount)
    final = {a.name: a.balance for a in dashboard_accounts(run) if a.balance is not None}
    record["final"] = final
    record["restored"] = all(final.get(n) == v for n, v in target.items())
    return jsonable(record)
