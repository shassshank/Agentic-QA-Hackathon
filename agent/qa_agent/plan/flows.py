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
from ..observe import (AccountView, read_account_detail, read_accounts, read_feedback, read_field_hints,
                       read_transactions)
from ..observe import read_transfer_balances
from .registry import ProbeBlocked, ProbeContext, jsonable

FROM = re.compile(r"^\s*(from|source)\b", re.I)
TO = re.compile(r"^\s*(to|destination)\b", re.I)
AMOUNT = re.compile(r"amount", re.I)
MEMO = re.compile(r"memo|note|reference", re.I)
SUBMIT = re.compile(r"transfer|submit|send|confirm", re.I)
SLOW_COMMIT_MS = 30_000

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


def account_key(account: AccountView | dict) -> str:
    """Unique handle for an account: its displayed identifier (masked number) when shown, else its name.

    Also used to pick form options, whose text carries the same identifier, so similar names
    ("Checking", "Checking Plus") cannot select the wrong account.
    """
    if isinstance(account, AccountView):
        return account.identifier_text or account.name
    return account.get("identifier_text") or account["name"]


def balances(snap: dict[str, Any]) -> dict[str, Decimal | None]:
    """Balance per account key; None when the balance could not be read."""
    return {account_key(a): Decimal(a["balance"]) if a.get("balance") is not None else None
            for a in snap["accounts"]}


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
    hints = read_field_hints(run.page)
    posts_before = len(run.recorder.api_calls("POST"))
    run.clear_timeline()
    submitted_at = now_ms()
    run.submit(SUBMIT, clicks=clicks, settle=False)
    settled = run.settle()
    if not settled:
        # A slow commit must finish (or visibly fail) before anyone reads balances or restores.
        settled = run.settle(timeout_ms=SLOW_COMMIT_MS)
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
        "settled": settled,
        "feedback": feedback,
        "field_hints": hints,
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


def window_delta(before: dict[str, Any], after: dict[str, Any], *, run_tag: str,
                 untagged_allowed: int) -> dict[str, Any]:
    """Balance change between two snapshots taken around our own action, and whether it is ours alone.

    New history rows that do not carry this run's tag are foreign activity (another team, a scheduled
    job). Rows we created without a memo are allowed up to the number of our committed submissions.
    """
    b, a = balances(before), balances(after)
    unreadable = sorted(k for k in set(b) | set(a) if b.get(k) is None or a.get(k) is None)
    delta = {k: a[k] - b[k] for k in b if k in a and b[k] is not None and a[k] is not None}
    before_keys = {r.get("id") or "|".join(str(v) for v in r.get("cells", {}).values()) for r in before["rows"]}
    new_rows = [r for r in after["rows"]
                if (r.get("id") or "|".join(str(v) for v in r.get("cells", {}).values())) not in before_keys]
    untagged = [r for r in new_rows if run_tag not in " ".join(str(v) for v in r.get("cells", {}).values())]
    return jsonable({
        "delta": delta,
        "unreadable": unreadable,
        "new_rows": len(new_rows),
        "foreign_rows": untagged[untagged_allowed:],
    })


def restore(ctx: ProbeContext, before: dict[str, Any], after: dict[str, Any] | None,
            *, committed_without_memo: int = 0) -> dict[str, Any]:
    """Reverse exactly the balance change our action caused (net-zero runs on a shared target).

    Only the before/after window around our own submission is compensated, never "current vs
    baseline", so another team's concurrent transfer is not undone. If foreign activity appears in the
    window or any balance is unreadable, nothing is compensated and the record says why; the run
    reports it instead of guessing.
    """
    if after is None:
        after = snapshot(ctx.run, "after-for-restore")
    window = window_delta(before, after, run_tag=ctx.run_tag, untagged_allowed=committed_without_memo)
    delta = {k: Decimal(v) for k, v in window["delta"].items()}
    record: dict[str, Any] = {"window": window, "needed": any(delta.values()), "transfers": []}
    if window["unreadable"]:
        record.update(restored=False, reason=f"balances unreadable for {window['unreadable']}")
        return jsonable(record)
    if window["foreign_rows"]:
        record.update(restored=not record["needed"],
                      reason="foreign transactions in the action window; change not attributable, not compensated")
        return jsonable(record)
    if not record["needed"]:
        record["restored"] = True
        return jsonable(record)

    run = ctx.run
    current = {account_key(a): a.balance for a in dashboard_accounts(run)}
    target = {k: current[k] - d for k, d in delta.items() if current.get(k) is not None}
    gainers = sorted(((k, d) for k, d in delta.items() if d > 0), key=lambda x: -x[1])
    losers = [[k, -d] for k, d in sorted(delta.items(), key=lambda x: x[1]) if d < 0]
    for gainer, gain in gainers:
        for pair in losers:
            loser, loss = pair
            if gain <= 0 or loss <= 0:
                continue
            amount = min(gain, loss)
            attempt = attempt_transfer(run, source=gainer, destination=loser, amount=f"{amount:.2f}",
                                       memo=ctx.memo("restore"), label="restore")
            record["transfers"].append({"from": gainer, "to": loser, "amount": f"{amount:.2f}",
                                        "settled": attempt["settled"], "posts": attempt["posts"],
                                        "feedback": attempt["feedback"]})
            gain -= amount
            pair[1] = loss - amount
    final = {account_key(a): a.balance for a in dashboard_accounts(run)}
    record["target"] = target
    record["final"] = final
    # Every account we changed must be observed again at its target; a missing balance is not success.
    record["restored"] = len(target) == len(delta) and all(final.get(k) == v for k, v in target.items())
    return jsonable(record)
