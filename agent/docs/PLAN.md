# DemoBank QA Agent — Development Plan

Status: approved planning baseline (2026-10-20). All work is committed locally only; nothing is
pushed until explicitly requested.

## 1. Goal

Given a target URL, the PRD (`test-website/PRD.md`) and optionally the target repo path, the agent
must:

1. Extract verifiable requirements from the PRD.
2. Generate and execute normal, negative and edge-case scenarios.
3. Explore and operate the web application autonomously.
4. Compare observed behavior with expected behavior.
5. Report passed / failed / unverified requirements with reproducible evidence and a cost breakdown.

Judging weights: accuracy 30, rigor 25, cost 20, resilience 15, evidence 10.

## 2. Decisions

| Topic | Decision |
|---|---|
| Language | Python 3.12 (uv-managed), Playwright for Python, Pydantic v2, pytest |
| LLM | Pluggable provider interface; **Gemini default** (Gemini Flash at higher reasoning for one-time interpretation/planning, Flash at low reasoning for recovery/judge calls, context caching). Claude is the alternative provider for cost comparison. Keys and model ids come from `agent/.env` (`GEMINI_API_KEY`). |
| Target during development | Local DemoBank (`test-website/LOCAL_SETUP.md`); hosted URL plugged in when organizers provide it |
| Repo | git at kit root; agent lives in `agent/`; target app untouched |
| Git policy | Local commits only; push / PR only on explicit per-request approval |

## 3. Facts from the kit that shape the design

- Only one customer exists and `create_session` always binds to it. If the hosted DB is shared,
  other teams' transfers can change balances mid-run. The agent tags its own records with a unique
  per-run memo and reconciles foreign changes (verdict becomes unverified, never a false fail).
- Seed state: checking `•••• 1042` = 4369.75, savings `•••• 7781` = 8275.00, 12 transactions across
  three types, three statuses, Jan–Sep 2025. Dates are filtered and displayed in America/Los_Angeles
  (stated in the UI hint).
- UI hooks: `data-testid` (account-card, account-balance, transaction-row, result-count,
  transfer-balance, active-customer, profile-name, profile-email), ARIA roles (`status`, `alert`),
  labelled form controls, filters mirrored in the URL. The `/api/*` JSON is a second evidence
  channel captured from browser network traffic.
- The target source is context, not an answer key. Suspected defects in the local source (zero and
  negative amounts accepted, no insufficient-funds check, exclusive end date) are recorded only in
  the dev-only eval set and are never hard-coded into the agent. The hosted build may differ.

## 4. Architecture

```
PRD ─► INTERPRET ─► PLAN ─► EXECUTE ─► VERIFY ─► REPORT
       LLM once,    LLM +     Playwright,   rule-based      JSON + HTML + MD,
       cached       boundary  no LLM unless oracles; cheap   coverage matrix,
                    generator recovery      judge for        cost ledger
                                            subjective checks
```

```
agent/
  pyproject.toml
  qa_agent/
    cli.py            # qa-agent run --url --prd [--repo] [--out] [--provider] [--budget]
    config.py
    models.py         # Requirement, Scenario, Step, Observation, Evidence, Verdict, CostEntry
    llm/              # provider interface, gemini.py (default), claude.py, token meter, response cache
    interpret/        # markdown → bullets → atomic requirements; coverage check
    plan/             # scenario templates, boundary-value generator, LLM scenario expansion
    explore/          # a11y-tree site map, semantic locator map (cached)
    execute/          # browser driver, primitives, settle logic, session manager, evidence recorder
    observe/          # DOM parsers: accounts, transaction tables, alerts, forms; vision fallback
    verify/           # ledger, invariants, cross-channel agreement, masking, judge
    verdict/          # aggregation, confirmation re-runs, unverified reasons
    recover/          # self-healing locators, recovery policies
    report/           # json, html (self-contained), markdown, cost table
  eval/               # DEV ONLY: known_issues.yaml, variant builders, scoring
  tests/
  docs/
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| interpret | Deterministic markdown split into PRD bullets (with line numbers), then one LLM call converts them to atomic requirements: id, source quote, line ref, area, testability, assertions, oracle type. Cached by PRD hash. Deterministic check: every PRD bullet maps to at least one requirement. |
| plan | Normal / negative / edge scenarios per requirement. Deterministic boundary generator: amounts (blank, `0`, `0.00`, `-5`, `abc`, `1.234`, `0.01`, exact balance, balance + 0.01, whitespace, very large) and date boundaries picked from observed transaction dates. |
| explore | Crawl navigation from the accessibility tree; one LLM call maps PRD concepts to role/name locators, cached and reused. |
| execute | Primitives `goto`, `click_by_intent`, `fill_by_label`, `select_by_label`, `settle` (network idle + loading roles cleared). Fresh browser context + demo session per scenario. Evidence per step: before/after screenshot, DOM excerpt, filtered `/api` HAR, console errors, Playwright trace. |
| observe | Deterministic parsers to structured data; vision fallback only on parse failure. |
| verify | Ledger of expected balances; UI vs API vs post-refresh agreement; every filtered row satisfies every filter; inclusive date boundaries; masking regexes; rejected actions leave balances and history unchanged. Subjective checks ("clear explanation", "readable label") use a cheap judge with quoted evidence. |
| verdict | FAIL only when reproduced on a fresh session; PASS only with positive evidence for all planned scenarios; UNVERIFIED with an explicit reason (interference, ambiguity, blocked step). |
| recover | Locator failure → fresh a11y snapshot → one cheap LLM re-resolve → cache. Policies: re-enter demo, return to dashboard, bounded retry with backoff, else unverified. |
| report | `report.json`, self-contained `report.html` (screenshots, repro steps, expected vs observed), `report.md`; coverage matrix; cost table (tokens × price table, runtime). |

### Safety and etiquette

- Low concurrency, no load testing, only the supplied app.
- Mutating scenarios use small amounts and unique memos; compensating transfers keep the run net-zero.
- No secrets in the repo; API keys live in `agent/.env` (gitignored), with `agent/.env.example` committed as the template.

### Cost strategy

Interpretation and plan are cached by input hash, so repeat runs spend tokens only on recovery and
judge calls. Every LLM call is metered per phase and reported. Target: cents per full run.

## 5. Reference requirement catalogue (draft)

The agent derives its own catalogue at runtime; this hand-written list is used to review its output
and to score coverage.

| Area | IDs | Topics |
|---|---|---|
| Session (SES) | 001–006 | Enter Demo without credentials; header shows customer + Log out; logout → landing; protected routes blocked after logout (direct nav); refresh keeps valid session; refresh after logout does not restore access |
| Accounts (ACC) | 001–005 | Checking + savings shown; name, masked id, type, USD balance; two decimals and no full identifier; account detail with balance + history; dashboard/detail balances agree with latest transactions |
| Transfers (TRF) | 001–010 | Form fields; same-account rejected with no side effects; amount validation (blank, zero, negative, non-numeric, over-precision); insufficient funds rejected with no side effects; atomic debit/credit; single completed record in both histories with all fields; success shown only after commit; failure explained with no success state; refresh preserves results |
| History (HIS) | 001–006 | Combined + per-account history; row fields + amount direction; filters by account/type/status/date, every row matches; inclusive start/end boundaries; empty state; clearing filters restores |
| Profile (PRO) | 001–002 | Synthetic name + masked email; no passwords, tokens, full ids or other users' data |
| General (GEN) | 001–005 | 1280×800 and narrow widths; labels and loading/success/validation/failure states; no silent disappearance or duplicate submits; nav links reach labeled destination; recoverable errors allow retry or return |
| Seed (SEED) | 001 | One customer, two accounts, multiple dates, at least one transfer, enough variety for filters |

## 6. Phases, owners and routing

| # | Phase | Owner | Done when |
|---|---|---|---|
| P0 | uv project, Playwright, local DemoBank running (Docker, uvicorn, Vite) | Claude | `qa-agent doctor` reports target healthy |
| P1 | Manual baseline: one requirement end to end with evidence; dev-only `eval/known_issues.yaml` | Claude + codex second pass | Baseline evidence bundle produced |
| P2 | PRD interpreter → `requirements.json` | Claude; agy (Opus 4.6) cross-check | User reviews catalogue once |
| P3 | Browser core + evidence recorder | Claude | Primitives + evidence covered by tests |
| P4 | Observers + scenarios per area | Claude | All areas have normal/negative/edge scenarios |
| P5 | Oracles, ledger, date/timezone logic | codex | Oracle unit tests green |
| P6 | Verdict engine + confirmation re-runs | codex, then codex adversarial review | No false fails on the fixed variant |
| P7 | Self-healing + recovery + chaos tests | codex | Passes on mutated-label and slow-network variants |
| P8 | HTML audit report design | agy (Gemini default) | Self-contained report renders with evidence |
| P9 | Eval harness: shipped / fixed / mutant variants, precision-recall | codex | Metrics table generated |
| P10 | Hosted run, architecture README, video script, exported report + cost | Claude; agy visual polish | Submission package complete |

Jev is used during development for small routing and triage judgments (noted in progress updates).

## 7. Open items

- Hosted DemoBank URL (organizers).
- `GEMINI_API_KEY` in `agent/.env` (user supplies). `ANTHROPIC_API_KEY` only if the Claude comparison run is wanted.
- Confirm exact Gemini API model ids against the API model list once the key is present (agy lists
  Gemini 3.8 Flash high/medium/low; API names may differ).
- agy cross-check model confirmed: `claude-opus-4-6-thinking`.
