# P5 — Oracles: observations → checks

Input: `runs/<run>/observations.json` written by `qa-agent probe` (shape: `{run_tag, base_url, complete,
planned, results: [ProbeResult]}`; `ProbeResult.facts` per probe is defined by `qa_agent/plan/probes.py`
and `qa_agent/plan/flows.py`). Per-probe evidence lives in `runs/<run>/evidence/<probe-id>/evidence.json`
(steps with screenshots, network records with request/response bodies, console) — reference it, do not
copy it. Reference data: `runs/p5-base/` (full run against the local build).

Output: `runs/<run>/checks.json` and a console summary via `qa-agent verify --run runs/<run>`.

## Rules

- Never encode a suspected defect. Expectations come from the PRD's stated behaviour and from data the
  probes observed (baseline rows, balances, options). A check must be able to pass on a correct build.
- Three outcomes: `pass` (positive evidence for every condition), `fail` (a condition observed false),
  `unknown` (cannot decide — probe blocked/errored, data missing, interference, or wording quality that
  needs the judge). Every `fail` and `unknown` carries a one-line reason.
- Interference: if a probe's `restore.reason` mentions foreign transactions, or `restore.window` shows
  foreign rows, any balance/history check from that probe is `unknown` ("concurrent activity"), never fail.
- Evidence on every check: `probe_id`, screenshot file names (from facts or the probe's evidence steps),
  and quoted observed values (short). Expected vs observed as short strings.
- Wording-quality conditions ("clear explanation", "readable label", "amount direction clear") produce a
  check with status `unknown`, `needs_judge: true` and `judge_input` (the exact visible text + context).
  P6 sends those to the cheap judge in one batched call.
- Pure functions over facts; no browser, no LLM, no network. Deterministic ordering.

## Check model (`qa_agent/verify/models.py`)

```
Check: id (e.g. "transfer.amount_validation.zero.no_side_effect"), behaviour (probe behaviour tag it
  serves, e.g. "transfer.amount_validation.zero"), area, title, status, reason, expected, observed,
  probe_id, evidence: {screenshots: [...], steps: [...], network: [...]}, needs_judge, judge_input
CheckReport: run_tag, base_url, generated_at, checks, summary {pass, fail, unknown}
```

## Checks (by behaviour tag; each bullet = one or more Check ids)

Session (`ses.*`)
- session.enter: landing has the enter action; after entering, path ≠ landing and account cards > 0.
- session.no_credentials: landing `password_inputs == 0` and `identity_inputs == 0`.
- header.customer / header.logout: header shows a non-empty customer label; header controls include a
  log-out control.
- session.logout: after logout, landing action visible and no protected content
  (account_cards = transaction_rows = 0, no transfer_form, no profile_fields, no header customer).
- session.protected_after_logout / session.protected_without_session: every visit shows no protected
  content AND (landing action visible OR an access-required alert/status). One check per visited path.
- session.refresh_keeps: every visit stays on the requested path with header customer present and its
  content present.
- session.refresh_after_logout: after_back / after_reload / after_direct_reload show no protected content.
- general.duplicate_submit (ses.double_enter): ends on a protected view with no alert.

Accounts (`acc.*`)
- accounts.list: a checking-like and a savings-like account are shown (type label or text).
- accounts.fields: per account — name, identifier_text, type_label, balance parseable, USD indicated
  (`$` or `USD` in balance_text).
- accounts.two_decimals: `balance_decimals == 2` on dashboard cards, detail balances, transfer side list.
- accounts.masking: identifier_text contains a run of non-digit mask characters and ≤ 6 digits; no digit
  run ≥ 8 in any page text/attribute collected by pro.no_secrets (exclude ISO dates/times and UUID-shaped
  URL ids inside href attributes — those are route ids, report them separately as `unknown` with reason).
- accounts.detail: per account — detail heading equals the account name, identifier matches, balance
  equals the dashboard balance, history present or an empty state.
- accounts.balance_consistency: dashboard balance == API balance (`api_accounts`) == detail balance ==
  transfer side balance, per account; after valid transfers, dashboard after == before ± amount.

Transfers (`trf.*`) — use `plan`, `before`, `attempt`, `after`, `restore`, optional `source_detail`,
`destination_detail`, `after_reload`.
- Committed = any POST in `attempt.posts` with 2xx. Success state = feedback status/alert text matching a
  success pattern (complete|success|transferred|done) and no error alert. New rows = after.rows − before.rows
  by key (id, else cell tuple).
- transfer.valid / transfer.amount_accept (trf.valid, memo_optional, min_amount, exact_balance): committed,
  success state, source delta = −amount, destination delta = +amount, all other accounts unchanged.
- transfer.atomic: both deltas equal in magnitude and opposite; never only one side changed.
- transfer.record: exactly one new row for this attempt (memo match when memo given, else new transfer
  row); that row: type transfer, status completed, amount equals plan amount, account text mentions source
  and destination identifiers, date present, memo shown when supplied; found in source_detail and
  destination_detail rows when those facts exist.
- transfer.success_after_commit: in `attempt.timeline`, the first success feedback `t` ≥ the POST
  `response` `t`; no success feedback without a 2xx response.
- transfer.refresh_persists: after_reload balances equal after balances; memo row still present.
- transfer.memo_optional: committed with memo empty; new row has a non-empty description.
- Rejections (trf.reject.*, trf.same_account, trf.insufficient): four checks each —
  `.rejected` (no 2xx POST), `.no_side_effect` (zero delta on every account, no new rows),
  `.no_success_state`, `.explained` (an alert, error status or field error is visible; text goes to
  `judge_input` with `needs_judge` for clarity — the presence part is deterministic).
- transfer.funds_boundary (exact_balance): accepted like a valid transfer.
- general.duplicate_submit (trf.double_submit): ≤ 1 POST observed and exactly one new memo row.
- general.loading_state (trf.slow_submit): a busy feedback (submitting|loading|processing|…|ellipsis) or a
  disabled submit appears between the POST request and response in the timeline.

History (`his.*`)
- history.combined: combined rows non-empty and equal (by id) to the union of account histories.
- history.account_specific: every account-history row mentions that account (identifier or name).
- history.row_fields: every row has non-empty date, description, type, account, amount, status.
- history.row_amount: UI amount magnitude equals the API transaction `amount` for the same id (match via
  `api_transactions` in the same fact block). Report mismatches with the row id, UI text and API amount.
- history.amount_direction: completed deposits show credit, completed withdrawals debit; text to judge.
- history.filter.account|type|status: per application — every row satisfies the predicate (account: row
  account text contains the option's identifier/name; type/status: label equality, case-insensitive),
  row set == baseline rows filtered by the same predicate, and non-empty when that expected set is
  non-empty. URL reflects the filter (informational, not a failure).
- history.filter.date / history.filter.date_inclusive: expected = baseline rows whose `occurred_at` local
  date (facts.timezone.zone) is within [start, end] (open ends allowed); set equality. `reversed` expects
  no rows (or an explanatory message). Boundary cases (single_day_*, boundaries_inner) are the inclusive
  checks; name the dates in the reason.
- history.filter.combined: row set == baseline ∩ all three predicates; non-empty.
- history.empty_state: no rows, empty text present, no error text, no alert.
- history.clear: after clearing, row set == baseline set and the path has no filter query.

Profile (`pro.*`)
- profile.fields: display name and email present.
- profile.masked_email: email shown is masked (mask characters or ellipsis in the local part; not a plain
  full address).
- profile.no_secrets: across pages text/attributes/storage and `api_responses` bodies — no password
  fields/values, no JWT-like tokens (`eyJ…\.…\.…`), no keys named password/token/secret/session with
  non-empty string values, no unmasked email addresses, no digit runs ≥ 8 outside dates/route ids.
  One check per category.

General (`gen.*`)
- general.viewport: per viewport × path — no horizontal overflow, no off-screen controls, ≥ 1 visible
  nav link, an h1 present.
- general.labels: no unlabeled controls in any view; transfer form controls all have labels.
- general.navigation: per link — the label's words overlap the destination path or h1 (normalise case,
  strip punctuation; small synonym map: funds→transfer, history→transactions, home→dashboard). No overlap
  → `unknown` + `needs_judge`. "Transfer from this account" also requires the account to be pre-selected.
- general.loading_state (gen.loading): per view, a busy feedback event occurs before the view's data
  response.
- general.recoverable_error: per view, an alert is shown with a retry or back-to-dashboard action, and
  after retry the view's content is present.
- general.not_found: unknown route offers an action back to the dashboard/home.

Seed (`seed.variety`, from his.combined)
- one active customer label; exactly one checking-like and one savings-like account; ≥ 2 distinct dates;
  ≥ 1 transfer row between the two accounts; ≥ 2 distinct values for type, status and account in rows.

## Deliverables

- `qa_agent/verify/{__init__,models,util,session,accounts,transfers,history,profile,general,seed,run}.py`
  (`run.evaluate(observations: dict) -> CheckReport`).
- `qa-agent verify --run PATH` in `cli.py`: writes checks.json, prints per-area pass/fail/unknown counts
  and the fail list.
- `tests/test_verify.py`: synthetic fact fixtures per area — each check family has a passing case and a
  failing case, plus interference → unknown, blocked probe → unknown.
- Running against `runs/p5-base` must not crash; every probe yields at least one check.
