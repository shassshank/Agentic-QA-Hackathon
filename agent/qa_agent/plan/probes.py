"""Probe library: normal, negative and edge scenarios for every PRD area.

Probes discover names, paths, options and boundary dates from the running app; nothing about the
expected outcome is encoded here. Each probe records facts; oracles decide.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..execute import ScenarioRun
from ..models import Area, ScenarioKind
from ..observe import (local_date, read_account_detail, read_accounts, read_feedback, read_header, read_layout, read_page_text,
                       read_profile, read_select_options, read_storage, read_transactions)
from .flows import (account_key, accounts_pair, account_detail, attempt_transfer, dashboard_accounts, last_api_json, nav_path,
                    restore, rows_with_memo, snapshot)
from .registry import ProbeBlocked, ProbeContext, jsonable, probe

N, NEG, EDGE = ScenarioKind.NORMAL, ScenarioKind.NEGATIVE, ScenarioKind.EDGE

ENTER = re.compile(r"enter (the )?demo|start (the )?demo|try (the )?demo", re.I)
LOGOUT = re.compile(r"log ?out|sign ?out", re.I)
APPLY = re.compile(r"^\s*(apply|search|filter)\b", re.I)
CLEAR = re.compile(r"clear|reset", re.I)
RETRY = re.compile(r"try again|retry|reload", re.I)
F_ACCOUNT = re.compile(r"^\s*account", re.I)
F_TYPE = re.compile(r"^\s*(transaction )?type", re.I)
F_STATUS = re.compile(r"^\s*status", re.I)
F_START = re.compile(r"start|from date", re.I)
F_END = re.compile(r"end|to date", re.I)

VIEWPORTS = [{"width": 1280, "height": 800}, {"width": 768, "height": 1024}, {"width": 390, "height": 844}]
TIMEZONES = [
    (re.compile(r"pacific|\bP[DS]T\b", re.I), "America/Los_Angeles"),
    (re.compile(r"mountain|\bM[DS]T\b", re.I), "America/Denver"),
    (re.compile(r"central|\bC[DS]T\b", re.I), "America/Chicago"),
    (re.compile(r"eastern|\bE[DS]T\b", re.I), "America/New_York"),
    (re.compile(r"\bUTC\b|\bGMT\b", re.I), "UTC"),
]


# --- shared helpers ------------------------------------------------------------------------------


def _visible_count(run: ScenarioRun, selector: str) -> int:
    return run.page.locator(selector).evaluate_all("els => els.filter(e => e.getClientRects().length > 0).length")


def _landing(run: ScenarioRun) -> dict[str, Any]:
    enter = run.page.get_by_role("button", name=ENTER)
    return {
        "path": run.path(),
        "enter_action_visible": enter.count() > 0 and enter.first.is_visible(),
        "enter_action_name": enter.first.inner_text().strip() if enter.count() else None,
        "password_inputs": _visible_count(run, "input[type=password]"),
        "identity_inputs": _visible_count(
            run, "input[type=email], input[autocomplete~=username], input[name*=user i], input[name*=ssn i]"),
        "text": read_page_text(run.page)[:2000],
    }


def _access_state(run: ScenarioRun) -> dict[str, Any]:
    """What a visitor can see and do on the current page (protected content vs landing/access state)."""
    feedback = read_feedback(run.page)
    header = read_header(run.page)
    return jsonable({
        "path": run.path(),
        "landing_enter_visible": run.page.get_by_role("button", name=ENTER).count() > 0,
        "header_customer": header.get("customer"),
        "logout_visible": run.page.get_by_role("button", name=LOGOUT).count() > 0,
        "account_cards": len(read_accounts(run.page)),
        "transaction_rows": len(read_transactions(run.page).rows),
        "transfer_form": run.page.get_by_label(re.compile(r"amount", re.I)).count() > 0,
        "profile_fields": bool(read_profile(run.page).get("fields")),
        "alerts": feedback.alerts,
        "statuses": feedback.statuses,
        "text": read_page_text(run.page)[:800],
    })


def _protected_paths(run: ScenarioRun) -> list[str]:
    paths = [nav_path(run, k) for k in ("dashboard", "transactions", "transfer", "profile")]
    accounts = dashboard_accounts(run)
    if accounts and accounts[0].href:
        paths.append(accounts[0].href)
    return list(dict.fromkeys(paths))


def _logout(run: ScenarioRun) -> None:
    run.click("button", LOGOUT, settle=False)
    try:
        run.page.get_by_role("button", name=ENTER).first.wait_for(state="visible", timeout=run.timeout_ms)
    except PlaywrightTimeout:
        pass  # outcome recorded by the caller; a missing landing page is itself an observation
    run.settle()


def _timezone(run: ScenarioRun, rows: list[dict]) -> tuple[str, str]:
    """Timezone the UI says it uses for date filters (hint text), else the zone shown in row times."""
    hint = ""
    try:
        described = run.page.get_by_label(F_START).first.get_attribute("aria-describedby", timeout=2000) or ""
        for ref in described.split():
            hint += " " + (run.page.locator(f"#{ref}").inner_text(timeout=2000) or "")
    except PlaywrightError:
        pass
    for source in (hint, " ".join(r.get("date_text") or "" for r in rows[:5])):
        for pattern, zone in TIMEZONES:
            if pattern.search(source):
                return zone, source.strip()[:200]
    return "UTC", "no timezone stated; assumed UTC"


def _apply_filters(run: ScenarioRun, values: dict[re.Pattern[str], str], *, shot: bool = False) -> dict[str, Any]:
    """Set every filter control (empty string clears it), apply, and read the result."""
    for label, value in values.items():
        control = run.page.get_by_label(label).first
        tag = control.evaluate("el => el.tagName")
        if tag == "SELECT":
            run.select(label, option_value=value)
        else:
            run.fill(label, value)
    run.click("button", APPLY, shot=shot)
    table = read_transactions(run.page)
    return jsonable({
        "filters": {label.pattern: value for label, value in values.items()},
        "path": run.path(),
        "present": table.present,
        "rows": table.rows,
        "count_text": table.count_text,
        "empty_text": table.empty_text,
        "error_text": table.error_text,
        "alerts": read_feedback(run.page).alerts,
        "api_transactions": last_api_json(run, "GET", "/transactions"),
    })


def _history_baseline(ctx: ProbeContext) -> list[dict]:
    run = ctx.run
    run.enter_demo()
    run.goto(nav_path(run, "transactions"))
    table = read_transactions(run.page)
    if not table.rows:
        raise ProbeBlocked("unfiltered history is empty; filters cannot be exercised")
    ctx.fact("baseline", {"path": run.path(), "rows": table.rows, "count_text": table.count_text,
                          "api_transactions": last_api_json(run, "GET", "/transactions")})
    return ctx.facts["baseline"]["rows"]


# --- session and access --------------------------------------------------------------------------


@probe("ses.enter_demo", Area.SES, N, "Enter the demo from the landing page without credentials",
       behaviours=["session.enter", "session.no_credentials", "header.customer", "header.logout"])
def ses_enter_demo(ctx: ProbeContext) -> None:
    run = ctx.run
    run.goto("/", shot=True)
    ctx.fact("landing", _landing(run))
    run.enter_demo()
    run.shot("dashboard")
    ctx.fact("after_enter", {"path": run.path(), "header": read_header(run.page),
                             "account_cards": len(read_accounts(run.page))})
    ctx.fact("session_posts", [{"url": p.url, "status": p.status} for p in run.recorder.api_calls("POST")])


@probe("ses.double_enter", Area.SES, EDGE, "Rapid double activation of Enter Demo",
       behaviours=["session.enter", "general.duplicate_submit"])
def ses_double_enter(ctx: ProbeContext) -> None:
    run = ctx.run
    run.goto("/")
    button = run.page.get_by_role("button", name=ENTER).first
    try:
        button.dblclick(timeout=run.timeout_ms)
    except PlaywrightError as exc:
        ctx.fact("click_error", str(exc).splitlines()[0])
    try:
        run.page.wait_for_url(re.compile(r"/(?!$)"), timeout=run.timeout_ms)
    except PlaywrightTimeout:
        pass
    run.settle()
    ctx.fact("after", _access_state(run))
    ctx.fact("session_posts", [{"url": p.url, "status": p.status} for p in run.recorder.api_calls("POST")])


@probe("ses.logout", Area.SES, N, "Log out returns to the landing page",
       behaviours=["session.logout", "header.logout"])
def ses_logout(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    ctx.fact("before", _access_state(run))
    _logout(run)
    run.shot("after-logout")
    ctx.fact("after", _access_state(run))
    ctx.fact("logout_calls", [{"url": p.url, "status": p.status} for p in run.recorder.api_calls("POST")
                              if "logout" in p.url or "session" in p.url])


@probe("ses.protected_after_logout", Area.SES, NEG, "Direct navigation to protected views after logout",
       behaviours=["session.protected_after_logout"])
def ses_protected_after_logout(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    paths = _protected_paths(run)
    _logout(run)
    for path in paths:
        run.goto(path)
        ctx.append("visits", {"requested": path, **_access_state(run)})
    run.shot("last-visit")


@probe("ses.no_session", Area.SES, NEG, "Protected views without ever entering the demo",
       behaviours=["session.protected_without_session"])
def ses_no_session(ctx: ProbeContext) -> None:
    with ctx.session("discover") as discover:
        discover.enter_demo()
        paths = _protected_paths(discover)
    run = ctx.run
    for path in paths:
        run.goto(path)
        ctx.append("visits", {"requested": path, **_access_state(run)})


@probe("ses.refresh_keeps_session", Area.SES, EDGE, "Refreshing protected views keeps a valid session",
       behaviours=["session.refresh_keeps"])
def ses_refresh_keeps_session(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    for path in _protected_paths(run):
        run.goto(path)
        run.reload()
        ctx.append("visits", {"requested": path, **_access_state(run)})


@probe("ses.refresh_after_logout", Area.SES, EDGE, "Back button and refresh after logout do not restore access",
       behaviours=["session.refresh_after_logout", "session.protected_after_logout"])
def ses_refresh_after_logout(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    run.goto(nav_path(run, "transactions"))
    _logout(run)
    try:
        run.page.go_back(wait_until="domcontentloaded", timeout=run.timeout_ms)
    except PlaywrightError:
        pass
    run.settle()
    ctx.fact("after_back", _access_state(run))
    run.reload()
    ctx.fact("after_reload", _access_state(run))
    run.goto(nav_path(run, "transactions"))
    run.reload()
    ctx.fact("after_direct_reload", _access_state(run))


# --- dashboard and accounts ----------------------------------------------------------------------


@probe("acc.dashboard", Area.ACC, N, "Dashboard lists the customer's accounts with masked ids and balances",
       behaviours=["accounts.list", "accounts.fields", "accounts.masking", "accounts.two_decimals"])
def acc_dashboard(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    run.shot("dashboard")
    ctx.fact("accounts", read_accounts(run.page))
    ctx.fact("api_accounts", last_api_json(run, "GET", "/accounts"))
    ctx.fact("page_text", read_page_text(run.page)[:4000])


@probe("acc.detail", Area.ACC, N, "Selecting each account opens its detail view with balance and history",
       behaviours=["accounts.detail", "accounts.balance_consistency", "history.account_specific"])
def acc_detail(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    accounts = read_accounts(run.page)
    if not accounts:
        raise ProbeBlocked("no accounts visible on the dashboard")
    ctx.fact("dashboard_accounts", accounts)
    # Resolve before waiting: Playwright calls inside a wait_for_url predicate deadlock the sync API.
    dashboard_url = run.base_url + nav_path(run, "dashboard")
    for index, account in enumerate(accounts):
        if index:
            run.goto(nav_path(run, "dashboard"))
        cards = run.page.get_by_test_id("account-card")
        target = cards.nth(index) if cards.count() > index else run.page.get_by_role(
            "link", name=re.compile(re.escape(account.name))).first
        target.click(timeout=run.timeout_ms)
        try:
            run.page.wait_for_url(lambda url: url.rstrip("/").split("?")[0] != dashboard_url, timeout=run.timeout_ms)
        except PlaywrightTimeout:
            pass
        run.settle()
        run.shot(f"detail-{index}")
        table = read_transactions(run.page)
        ctx.append("details", {"account": account.name, "clicked_from": "dashboard",
                               "detail": read_account_detail(run.page, run.path()), "rows": table.rows,
                               "empty_text": table.empty_text, "error_text": table.error_text,
                               "api_transactions": last_api_json(run, "GET", "/transactions")})
    run.goto(nav_path(run, "transactions"))
    ctx.fact("combined_rows", read_transactions(run.page).rows)


@probe("acc.unknown_account", Area.ACC, NEG, "Detail view for an account id that does not exist",
       behaviours=["accounts.detail", "general.recoverable_error"])
def acc_unknown_account(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    accounts = read_accounts(run.page)
    if not accounts or not accounts[0].href:
        raise ProbeBlocked("no account detail link to derive a missing id from")
    missing = accounts[0].href.rstrip("/").rsplit("/", 1)[0] + f"/qa-missing-{ctx.run_tag}"
    run.goto(missing, shot=True)
    ctx.fact("requested", missing)
    ctx.fact("state", _access_state(run))
    ctx.fact("actions", run.page.locator("main a, main button").all_inner_texts())


# --- transfers -----------------------------------------------------------------------------------


@probe("trf.form", Area.TRF, N, "Transfer form exposes source, destination, amount and optional memo",
       behaviours=["transfer.form_fields", "general.labels"])
def trf_form(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    accounts = read_accounts(run.page)
    run.goto(nav_path(run, "transfer"), shot=True)
    controls = run.page.locator("form input, form select, form textarea").evaluate_all("""els => els.map(el => ({
        tag: el.tagName.toLowerCase(), type: el.type || null, name: el.name || null, required: el.required,
        label: el.labels && el.labels.length ? el.labels[0].innerText.trim() : el.getAttribute('aria-label'),
        options: el.tagName === 'SELECT' ? [...el.options].map(o => o.textContent.trim()) : null }))""")
    ctx.fact("controls", controls)
    ctx.fact("submit_buttons", run.page.locator("form button").all_inner_texts())
    ctx.fact("dashboard_accounts", [a.name for a in accounts])
    ctx.fact("layout", read_layout(run.page))


def _transfer_case(ctx: ProbeContext, *, amount: str | None = None, amount_of: str | None = None,
                   delta: Decimal = Decimal("0"), memo_label: str | None = None, same_account: bool = False,
                   clicks: int = 1, latency_ms: int | None = None, verify_histories: bool = False,
                   reload_after: bool = False) -> None:
    """Snapshot → one transfer attempt → snapshot → (optional history/refresh checks) → restore.

    amount_of="source" derives the amount from the observed source balance (+ delta), e.g. exact
    balance or balance + 0.01, so boundaries follow whatever the live data is.
    """
    run = ctx.run
    run.enter_demo()
    source, destination = accounts_pair(read_accounts(run.page))
    if same_account:
        destination = source
    if amount_of == "source":
        if source.balance is None:
            raise ProbeBlocked("source balance could not be read")
        amount = f"{source.balance + delta:.2f}"
    assert amount is not None
    memo = ctx.memo(memo_label) if memo_label else None
    ctx.fact("plan", {"source": source.name, "destination": destination.name, "source_key": account_key(source),
                      "destination_key": account_key(destination), "amount": amount, "memo": memo,
                      "clicks": clicks, "latency_ms": latency_ms})
    before = ctx.fact("before", snapshot(run, "before"))
    after: dict | None = None
    try:
        if latency_ms:
            run.throttle(latency_ms)
        ctx.fact("attempt", attempt_transfer(run, source=account_key(source),
                                                       destination=account_key(destination), amount=amount,
                                                       memo=memo, clicks=clicks))
        if latency_ms:
            run.throttle(0)
        after = ctx.fact("after", snapshot(run, "after"))
        if verify_histories:
            ctx.fact("source_detail", account_detail(run, source))
            ctx.fact("destination_detail", account_detail(run, destination))
        if reload_after:
            run.goto(nav_path(run, "transactions"))
            run.reload()
            reloaded = read_transactions(run.page)
            run.goto(nav_path(run, "dashboard"))
            run.reload()
            ctx.fact("after_reload", {"rows": reloaded.rows, "accounts": read_accounts(run.page),
                                      "memo_rows": rows_with_memo(jsonable(reloaded.rows), memo) if memo else None})
    finally:
        # Runs even when an observation step fails after submission: a committed transfer must not be
        # left behind. Without an "after" snapshot, restore takes one now (wider window, still ours).
        committed = ctx.facts.get("attempt", {}).get("posts", [])
        untagged = sum(1 for p in committed if (p.get("status") or 0) < 300) if not memo else 0
        try:
            if latency_ms:
                run.throttle(0)
            ctx.fact("restore", restore(ctx, before, after, committed_without_memo=untagged))
        except Exception as exc:  # noqa: BLE001 - never mask the original failure; record cleanup failure
            ctx.fact("restore", {"restored": False, "reason": f"restore failed: {exc.__class__.__name__}: {exc}"})


@probe("trf.valid", Area.TRF, N, "Valid transfer with memo between the two owned accounts",
       behaviours=["transfer.valid", "transfer.atomic", "transfer.record", "transfer.success_after_commit",
                   "transfer.refresh_persists", "history.account_specific"], mutating=True)
def trf_valid(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="1.00", memo_label="valid", verify_histories=True, reload_after=True)


@probe("trf.memo_optional", Area.TRF, N, "Valid transfer with the optional memo left empty",
       behaviours=["transfer.valid", "transfer.memo_optional", "transfer.record"], mutating=True)
def trf_memo_optional(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="2.00", verify_histories=True)


@probe("trf.min_amount", Area.TRF, EDGE, "Smallest valid amount (0.01)",
       behaviours=["transfer.valid", "transfer.amount_accept"], mutating=True)
def trf_min_amount(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="0.01", memo_label="min")


@probe("trf.exact_balance", Area.TRF, EDGE, "Transfer of exactly the source's available balance",
       behaviours=["transfer.funds_boundary", "transfer.amount_accept"], mutating=True)
def trf_exact_balance(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount_of="source", memo_label="exact")


@probe("trf.insufficient", Area.TRF, NEG, "Amount one cent above the source's available balance",
       behaviours=["transfer.insufficient_funds", "transfer.no_side_effect", "transfer.failure_explained"],
       mutating=True)
def trf_insufficient(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount_of="source", delta=Decimal("0.01"), memo_label="nsf")


@probe("trf.same_account", Area.TRF, NEG, "Source and destination are the same account",
       behaviours=["transfer.same_account", "transfer.no_side_effect", "transfer.failure_explained"],
       mutating=True)
def trf_same_account(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="1.00", memo_label="same", same_account=True)


INVALID_AMOUNTS = {
    "blank": "",
    "whitespace": "   ",
    "zero": "0",
    "zero_decimal": "0.00",
    "negative": "-5.00",
    "non_numeric": "abc",
    "over_precision": "1.234",
}

for _case, _value in INVALID_AMOUNTS.items():
    def _make(value: str, case: str):
        def run_case(ctx: ProbeContext) -> None:
            _transfer_case(ctx, amount=value, memo_label=f"inv-{case}")
        return run_case

    probe(f"trf.reject.{_case}", Area.TRF, NEG, f"Invalid amount rejected: {_case} ({_value!r})",
          behaviours=["transfer.amount_validation", f"transfer.amount_validation.{_case}",
                      "transfer.no_side_effect", "transfer.failure_explained"], mutating=True)(_make(_value, _case))


@probe("trf.double_submit", Area.TRF, EDGE, "Rapid repeated clicks on submit create at most one transfer",
       behaviours=["general.duplicate_submit", "transfer.record"], mutating=True)
def trf_double_submit(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="0.01", memo_label="dbl", clicks=3)


@probe("trf.slow_submit", Area.TRF, EDGE, "Submitting on a slow network shows a waiting state",
       behaviours=["general.loading_state", "transfer.success_after_commit", "general.duplicate_submit"],
       mutating=True)
def trf_slow_submit(ctx: ProbeContext) -> None:
    _transfer_case(ctx, amount="0.01", memo_label="slow", latency_ms=1500)


# --- transaction history -------------------------------------------------------------------------


@probe("his.combined", Area.HIS, N, "Combined history and each account-specific history",
       behaviours=["history.combined", "history.account_specific", "history.row_fields", "seed.variety"])
def his_combined(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    accounts = read_accounts(run.page)
    ctx.fact("accounts", accounts)
    ctx.fact("header", read_header(run.page))
    run.goto(nav_path(run, "transactions"), shot=True)
    table = read_transactions(run.page)
    ctx.fact("combined", {"headers": table.headers, "rows": table.rows, "count_text": table.count_text,
                          "api_transactions": last_api_json(run, "GET", "/transactions")})
    for account in accounts:
        ctx.append("account_histories", {"account": account.name, **account_detail(run, account)})


def _filter_by_options(ctx: ProbeContext, label: re.Pattern[str]) -> None:
    run = ctx.run
    _history_baseline(ctx)
    options = [o for o in read_select_options(run.page, label) if o.value]
    if not options:
        raise ProbeBlocked(f"filter {label.pattern!r} has no options")
    ctx.fact("options", options)
    for option in options:
        result = _apply_filters(run, {label: option.value})
        ctx.append("applications", {"option": jsonable(option), **result})


@probe("his.filter.account", Area.HIS, N, "Filter history by each account",
       behaviours=["history.filter.account"])
def his_filter_account(ctx: ProbeContext) -> None:
    _filter_by_options(ctx, F_ACCOUNT)


@probe("his.filter.type", Area.HIS, N, "Filter history by each transaction type",
       behaviours=["history.filter.type"])
def his_filter_type(ctx: ProbeContext) -> None:
    _filter_by_options(ctx, F_TYPE)


@probe("his.filter.status", Area.HIS, N, "Filter history by each status",
       behaviours=["history.filter.status"])
def his_filter_status(ctx: ProbeContext) -> None:
    _filter_by_options(ctx, F_STATUS)


@probe("his.filter.dates", Area.HIS, EDGE, "Date-range filters on boundary dates taken from the data",
       behaviours=["history.filter.date", "history.filter.date_inclusive"])
def his_filter_dates(ctx: ProbeContext) -> None:
    run = ctx.run
    rows = _history_baseline(ctx)
    zone, basis = _timezone(run, rows)
    ctx.fact("timezone", {"zone": zone, "basis": basis})
    dates = sorted({d for r in rows if (d := local_date(r.get("occurred_at"), zone))})
    if len(dates) < 3:
        raise ProbeBlocked(f"need at least three distinct transaction dates, found {len(dates)}")
    mid = dates[len(dates) // 2]
    cases = {
        "single_day_first": (dates[0], dates[0]),
        "single_day_mid": (mid, mid),
        "boundaries_inner": (dates[1], dates[-2]),
        "start_only": (mid, None),
        "end_only": (None, mid),
        "reversed": (dates[-2], dates[1]),
    }
    for name, (start, end) in cases.items():
        result = _apply_filters(run, {F_START: start.isoformat() if start else "",
                                      F_END: end.isoformat() if end else ""}, shot=name == "single_day_first")
        ctx.append("applications", {"case": name, "start": start.isoformat() if start else None,
                                    "end": end.isoformat() if end else None, **result})


@probe("his.filter.combined", Area.HIS, N, "Account, type and status filters combined",
       behaviours=["history.filter.combined"])
def his_filter_combined(ctx: ProbeContext) -> None:
    run = ctx.run
    rows = _history_baseline(ctx)
    accounts = [o for o in read_select_options(run.page, F_ACCOUNT) if o.value]
    types = [o for o in read_select_options(run.page, F_TYPE) if o.value]
    statuses = [o for o in read_select_options(run.page, F_STATUS) if o.value]
    for row in rows:
        acc = next((o for o in accounts if o.label and o.label in (row.get("account") or "")), None)
        typ = next((o for o in types if o.label.lower() == (row.get("type") or "").lower()), None)
        sta = next((o for o in statuses if o.label.lower() == (row.get("status") or "").lower()), None)
        if acc and typ and sta and (row.get("type") or "").lower() != "transfer":
            break
    else:
        raise ProbeBlocked("no row whose account, type and status all map to filter options")
    ctx.fact("seed_row", row)
    ctx.fact("application", _apply_filters(run, {F_ACCOUNT: acc.value, F_TYPE: typ.value, F_STATUS: sta.value},
                                           shot=True))


@probe("his.empty", Area.HIS, EDGE, "Filters that match nothing show an empty state; clearing restores",
       behaviours=["history.empty_state", "history.clear"])
def his_empty(ctx: ProbeContext) -> None:
    run = ctx.run
    rows = _history_baseline(ctx)
    zone, _ = _timezone(run, rows)
    earliest = min((d for r in rows if (d := local_date(r.get("occurred_at"), zone))), default=None)
    year = (earliest.year if earliest else 2000) - 10
    ctx.fact("application", _apply_filters(run, {F_START: f"{year}-01-01", F_END: f"{year}-01-02"}, shot=True))
    ctx.fact("empty_state_actions", run.page.locator("main [role=status] button, main [role=status] a")
             .all_inner_texts())
    clear = run.page.get_by_role("button", name=CLEAR)
    if not clear.count():
        raise ProbeBlocked("no clear/reset control found")
    run.click("button", CLEAR)
    table = read_transactions(run.page)
    ctx.fact("after_clear", {"path": run.path(), "rows": table.rows, "count_text": table.count_text})


@probe("his.clear", Area.HIS, N, "Clearing an applied filter restores the unfiltered history",
       behaviours=["history.clear"])
def his_clear(ctx: ProbeContext) -> None:
    run = ctx.run
    _history_baseline(ctx)
    option = next((o for o in read_select_options(run.page, F_TYPE) if o.value), None)
    if option is None:
        raise ProbeBlocked("no type filter option to apply")
    ctx.fact("application", _apply_filters(run, {F_TYPE: option.value}))
    run.click("button", CLEAR, shot=True)
    table = read_transactions(run.page)
    controls = run.page.locator("form select, form input").evaluate_all("els => els.map(e => e.value)")
    ctx.fact("after_clear", {"path": run.path(), "rows": table.rows, "count_text": table.count_text,
                             "control_values": controls})


# --- profile -------------------------------------------------------------------------------------


@probe("pro.view", Area.PRO, N, "Profile shows synthetic display name and masked email",
       behaviours=["profile.fields", "profile.masked_email"])
def pro_view(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    run.goto(nav_path(run, "profile"), shot=True)
    ctx.fact("profile", read_profile(run.page))
    ctx.fact("header", read_header(run.page))
    ctx.fact("api_me", last_api_json(run, "GET", "/me"))


@probe("pro.no_secrets", Area.PRO, NEG, "No passwords, tokens, full identifiers or other users' data anywhere",
       behaviours=["profile.no_secrets", "accounts.masking"])
def pro_no_secrets(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    accounts = read_accounts(run.page)
    paths = [nav_path(run, k) for k in ("dashboard", "transactions", "transfer", "profile")]
    paths += [a.href for a in accounts if a.href]
    for path in dict.fromkeys(paths):
        run.goto(path)
        html_attrs = run.page.evaluate("""() => [...document.querySelectorAll('*')].flatMap(el =>
            [...el.attributes].filter(a => /^(data-|aria-|title|value|href)/.test(a.name)).map(a => a.value))
            .filter(v => v && v.length > 5).slice(0, 2000)""")
        ctx.append("pages", {"path": path, "text": read_page_text(run.page)[:6000], "attributes": html_attrs,
                             "storage": read_storage(run.page)})
    ctx.fact("api_responses", [{"url": r.url, "status": r.status, "body": r.response_body}
                               for r in run.recorder.network if r.response_body is not None][:60])


# --- general interface ---------------------------------------------------------------------------


@probe("gen.viewports", Area.GEN, EDGE, "Main views at desktop, tablet and phone widths",
       behaviours=["general.viewport", "general.labels"])
def gen_viewports(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    paths = [nav_path(run, k) for k in ("dashboard", "transactions", "transfer", "profile")]
    for viewport in VIEWPORTS:
        run.page.set_viewport_size(viewport)
        for path in paths:
            run.goto(path)
            nav_links = run.page.get_by_role("navigation").get_by_role("link")
            visible_nav = sum(1 for i in range(nav_links.count()) if nav_links.nth(i).is_visible())
            shot = run.shot(f"{viewport['width']}-{path.strip('/') or 'root'}") if viewport["width"] < 1000 else None
            ctx.append("views", {"viewport": viewport, "path": path, "layout": read_layout(run.page),
                                 "visible_nav_links": visible_nav, "h1": run.page.locator("h1").all_inner_texts(),
                                 "account_cards": len(read_accounts(run.page)),
                                 "transaction_rows": len(read_transactions(run.page).rows), "screenshot": shot})


@probe("gen.nav", Area.GEN, N, "Navigation links and page buttons lead to their labeled destinations",
       behaviours=["general.navigation"])
def gen_nav(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    home = run.path()
    nav = run.page.get_by_role("navigation").get_by_role("link")
    labels = [nav.nth(i).inner_text().strip() for i in range(nav.count())]
    for label in labels:
        run.goto(home)
        run.click("link", re.compile(rf"^\s*{re.escape(label)}\s*$"))
        ctx.append("nav", {"label": label, "path": run.path(), "h1": run.page.locator("h1").all_inner_texts()})
    run.goto(home)
    ctas = [t.strip() for t in run.page.locator("main a:not([data-testid=account-card])").all_inner_texts()
            if t.strip()]
    for label in ctas:
        run.goto(home)
        run.click("link", re.compile(rf"^\s*{re.escape(label)}\s*$"))
        ctx.append("page_links", {"from": home, "label": label, "path": run.path(),
                                  "h1": run.page.locator("h1").all_inner_texts()})
    accounts = dashboard_accounts(run)
    if accounts and accounts[0].href:
        run.goto(accounts[0].href)
        detail_links = [t.strip() for t in run.page.locator("main a").all_inner_texts()]
        for label in detail_links:
            run.goto(accounts[0].href)
            run.click("link", re.compile(rf"^\s*{re.escape(label)}\s*$"))
            selected = run.page.locator("form select").evaluate_all(
                "els => els.map(e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : '')")
            ctx.append("page_links", {"from": accounts[0].href, "account": accounts[0].name, "label": label,
                                      "path": run.path(), "h1": run.page.locator("h1").all_inner_texts(),
                                      "selected_options": selected})


@probe("gen.loading", Area.GEN, EDGE, "Views announce loading while data is on its way (slow network)",
       behaviours=["general.loading_state"])
def gen_loading(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    paths = [nav_path(run, k) for k in ("dashboard", "transactions", "transfer", "profile")]
    run.throttle(600)
    for path in paths:
        run.goto(path)
        ctx.append("views", {"path": path, "timeline": run.timeline(), "h1": run.page.locator("h1").all_inner_texts()})
    run.throttle(0)


@probe("gen.recoverable_error", Area.GEN, NEG, "A failed data load offers retry or a way back",
       behaviours=["general.recoverable_error", "general.failure_state"])
def gen_recoverable_error(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    # Inject one failure into the browser's own request for this view's data (client-side only).
    endpoints = {r.url.split("?")[0] for r in run.recorder.api_calls("GET") if r.url.split("?")[0].endswith("/accounts")}
    if not endpoints:
        raise ProbeBlocked("no account-list API call observed to fail")
    endpoint = sorted(endpoints)[0]
    for view in ("transfer", "dashboard"):
        # Fail every GET while the view loads (apps may fetch twice, e.g. React StrictMode aborting the
        # first request), then lift the failure before retrying so recovery is genuinely possible.
        injected = {"count": 0}

        def fail(route: Route) -> None:
            if route.request.method == "GET":
                injected["count"] += 1
                route.fulfill(status=503, content_type="application/json",
                              body='{"error":{"code":"qa_injected","message":"Injected failure"}}')
            else:
                route.continue_()

        pattern = re.compile(re.escape(endpoint) + r"(\?.*)?$")
        run.page.route(pattern, fail)
        run.goto(nav_path(run, view))
        state = _access_state(run)
        actions = run.page.locator("main a, main button").all_inner_texts()
        run.shot(f"error-{view}")
        run.page.unroute(pattern)
        retried: dict[str, Any] | None = None
        retry = run.page.get_by_role("button", name=RETRY)
        if retry.count():
            run.click("button", RETRY)
            retried = _access_state(run)
        ctx.append("views", {"view": view, "injected": endpoint, "injected_failures": injected["count"],
                             "state": state, "actions": actions, "after_retry": retried})


@probe("gen.not_found", Area.GEN, EDGE, "Unknown route inside the app offers a way back",
       behaviours=["general.recoverable_error", "general.navigation"])
def gen_not_found(ctx: ProbeContext) -> None:
    run = ctx.run
    run.enter_demo()
    run.goto(f"/qa-unknown-{ctx.run_tag}", shot=True)
    ctx.fact("state", _access_state(run))
    ctx.fact("actions", run.page.locator("main a, main button").all_inner_texts())
