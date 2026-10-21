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
| Decisions | **Jev (TypeSafe System One) is the default for small typed decisions**: wording judge, failure review, requirement→check linking, locator-heal fallback. Gemini stays for generation (PRD interpretation) and as the escalation path for low-confidence Jev answers. Key from `agent/.env` (`TYPESAFE_API_KEY`). See §4 "Decision layer". |
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

### Decision layer (Jev)

Jev returns typed answers and probabilities (noul = P(yes), choice = option + distribution) instead of
generated text. It is priced at $0.042 per million input tokens with free output (docs, 2026-10-20),
answers in roughly 70–500 ms and has its own quota, so it removes most calls from the 20-requests/day Gemini
free tier. Policy: **code decides what is computable, Jev decides narrow semantic questions, Gemini handles
generation and the uncertain band, and anything still uncertain stays unknown/unverified** (never a guess).

| Call site | Today | Jev question | Accept | Otherwise |
|---|---|---|---|---|
| Wording judge (`verdict/judge.py`) | Gemini fast, rationale must quote visible text | One narrow **noul** per condition over the check's visible strings (e.g. "does the message say what is wrong or how to fix it", "does `amount_text` alone show in/out"). Sign-vs-type and other computable parts stay in code. | p ≥ 0.85 → pass; p ≤ 0.15 → fail (still goes through confirm + failure review) | escalate to Gemini judge; no LLM → unknown |
| Failure review (`verdict/review.py`) | Gemini fast: violates / consistent / unsure | **choice** {violates, condition_not_met, different_behaviour, unsure} per failed requirement, state = requirement, assertions, failing checks | P(violates) ≥ 0.90 keeps FAILED; P(condition_not_met + different_behaviour) ≥ 0.90 → `link_disputed` | escalate to Gemini review; none → `unreviewed` |
| Linking (`verdict/link.py`) | One Gemini planner call | two stages: **choice** over families ranks; then a **noul** per (assertion, top-3 family or judged wording group) with scenario titles + checks as state | noul ≥ 0.80 | only the uncertain requirements go to the Gemini link call; coverage still computed per assertion |
| Heal fallback (`recover/heal.py`) | optional Gemini, confidence ≥ 0.85 | **choice** over snapshot candidates plus `none`, after deterministic Jaccard matching | p ≥ 0.85 and not `none` | block with reason (no submit replay, unchanged) |
| Interpret (`interpret/`) | Gemini planner, cached | not replaced (needs generation); optional **noul** citation check "does the source quote support this requirement" to flag invented requirements | p ≥ 0.5 | flag in the catalogue review |

Implementation notes:
- `qa_agent/llm/typesafe.py`: thin `DecisionClient` (`noul`, `choice`, batched over items) over
  `POST https://api.typesafe.ai/v1/systemone` (or `typesafe-sdk`), request timeout, retry on 429/529 with
  backoff, `DecisionUnavailable` on outage. Separate protocol from `LLMClient` because it never generates text.
- Model pinned (`jev-1.13.0`, the `jev-latest` alias) for reproducible verdicts; `jev-1.14.0` (preview) is
  evaluated in P9 before switching.
- Cache key = model + question text + criteria + state, same disk cache as other phases; probabilities and
  thresholds are stored in the verdict evidence so a reader can see why a check passed or escalated.
- Meter: provider `typesafe`, input tokens × $0.042/Mtok, output $0; shown as its own row in the cost table
  next to Gemini so the report shows the saving.
- Questions follow the Jev guidance: one narrow condition each, raw observed evidence as state (never our
  verdict), boundary cases in criteria, a `none`/`unsure` option where nothing may fit.

Trial on real P7 run data (2026-10-20, `runs/p7b-base`, 9 items): broad "is this clear" noul ranked the
real messages and obvious bad controls correctly but missed two (rejected a valid "Enter an amount" hint at
0.02, accepted a bare `$300.00` withdrawal at 0.76). Narrow per-condition nouls fixed both (0.65 → escalation
band, 0.07 → fail). ~440 input tokens per item ≈ $0.00002. Hence narrow questions + thresholds + escalation.

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
| P7J | Jev decision layer (§4): `DecisionClient`, judge/review/link/heal on Jev with Gemini escalation, cost meter row, unit tests with a fake client | codex (⚡ Jev routing: codex, confidence 1.0) | Full run with Gemini disabled still reaches verdicts for judge/review/link; same verdicts as the Gemini run on `runs/p5-base` except documented escalations; Jev cost row in ledger |
| P8 | HTML audit report design | agy (Gemini default) | Self-contained report renders with evidence |
| P9 | Eval harness: shipped / fixed / mutant variants, precision-recall; Jev vs Gemini agreement and threshold calibration per call site; jev-1.13 vs 1.14 | codex | Metrics table generated |
| P10 | Hosted run, architecture README, video script, exported report + cost | Claude; agy visual polish | Submission package complete |

Jev is used during development for small routing and triage judgments (noted in progress updates).

## 7. Open items

- Hosted DemoBank URL (organizers).
- `ANTHROPIC_API_KEY` only if the Claude comparison run is wanted.
- `TYPESAFE_API_KEY` for the agent's Jev decision layer (P7J) in `agent/.env`; the MCP access used during
  development does not give the agent a key. Confirm hackathon rules allow a third-party decision API.
- **Gemini key is free tier: 20 requests / model / day.** Development, eval runs and the demo share
  this. Decide whether to enable billing; otherwise every phase must stay cache-first and judge calls
  must be batched.
- Reference catalogue: v3 from `gemini-3.8-flash` (67 requirements) needs one repair call for 4 bullets
  whose "clear"/"readable" wording lacks a judge assertion (B04, B09, B23, B26) once quota resets.

## 7a. Inputs to P4/P5 from the agy (Opus 4.6) catalogue review

- Planner default edge/negative cases: single-day date range, open-ended range (start only / end only),
  start after end, transfer of the exact balance, same-account savings→savings, clearing filters from an
  empty result, refresh on non-dashboard protected views, rapid double click on Enter Demo and on submit.
- Filter oracle: expected rows = unfiltered history ∩ predicate; assert set equality (no extra rows, no
  missing rows) and non-empty when matches exist.
- Duplicate-submit oracle: count POSTs in captured network traffic and check the UI busy/disabled state.
- Masking oracle: "not all characters of the identifier visible", never a specific mask character.
- Pricing table for the cost report: confirm current per-token prices for the chosen Gemini models.

## 8. Resolved

- `GEMINI_API_KEY` present in `agent/.env`.
- Gemini API model ids confirmed (2026-10-20): planner `gemini-3.8-flash`, fast `gemini-3.5-flash-lite`,
  fallbacks `gemini-3.7-flash`, `gemini-3.5-flash`. `gemini-3.8-flash` returned 503 (high demand) or hung
  during P0, so every LLM call needs a request timeout, retry with backoff and the fallback chain.
- agy cross-check model confirmed: `claude-opus-4-6-thinking`.
- P0 done: local DemoBank (Postgres in Docker, uvicorn :8000, Vite :5173) and `qa-agent doctor` green.
- P4 done (2026-10-20): `observe/` readers (test id first, then table headers / ARIA / labels) and
  `plan/` probe library — 41 probes (SES 7, ACC 3, TRF 16, HIS 8, PRO 2, GEN 5), each normal / negative /
  edge, no LLM. `qa-agent probe` writes `observations.json` + per-probe evidence (steps, screenshots, API
  traffic, browser-clock timeline, trace). Full local run ≈ 3 min; 15/15 mutating probes restore balances.
  - Harness fix: Playwright's `networkidle` fires once per document, so SPA transitions now settle on an
    in-page in-flight request counter + quiet window; busy detection ignores long status regions (a
    receipt mentioning "Everyday Checking" was read as "checking…").
  - Facts already show (pending oracles): zero / negative / overdraft accepted with a success receipt,
    single-day date ranges on transaction dates return nothing, pending/failed rows display $0.00.
  - Restores create compensating records tagged with the run memo; history grows by those records.
- P5 done (2026-10-20): `verify/` oracles per `docs/P5_ORACLES.md` (codex implementation, reviewed):
  `qa-agent verify --run runs/<run>` → `checks.json`. Reference run: 571 checks over 41 probes —
  505 pass, 29 fail, 37 unknown (35 wording-quality items queued for the P6 judge).
  - Review fixes: row-level history checks run once where history is the subject (were repeated in
    every probe's snapshots: 46 duplicate fails); UUID record ids are reported as an interpretation
    question (unknown), not as exposed account numbers.
  - Remaining fails are app behaviour: zero / 0.00 / negative / over-balance amounts accepted with a
    success receipt; inclusive end date not honoured (single-day and end-bounded ranges); pending and
    failed rows show $0.00 instead of the transaction amount.
  - Known limitation: `verify/util.content()` assumes route names (/dashboard, /transfer, /profile) for
    "content present" checks; P7 should take paths from the probe's discovered navigation.
- P6 built (2026-10-20): `verdict/` per `docs/P6_VERDICTS.md` (codex implementation, reviewed) — LLM link
  (cached), batched judge (cached), confirmation re-runs, conservative aggregation; `qa-agent verdict`
  and `qa-agent run`. Review fix: a fail never re-run is `unconfirmed`, not `probe_blocked`.
  - Verified live without LLM (hand-written dev link file, no judge): 67 requirements → 41 passed,
    7 failed, 19 unverified; all 7 fails reproduced on fresh re-runs and match the dev-only known issues
    (zero, negative, over-balance, exclusive end date); balances restored after the re-runs.
  - Verified LLM outage path: all models over quota → every requirement unverified (llm_unavailable),
    no crash, no pass.
  - Pending: live link + judge calls (free-tier quota exhausted until reset); P8 should render steps in
    plain words (they currently show selector patterns).
- P6 hardening (2026-10-20), exercised end to end with a local dev LLM stand-in (uncommitted, not part of
  the submission) while the Gemini quota was exhausted:
  - Empty/unparseable link replies are an outage, never cached (they had been cached as "no links").
  - Family catalogue now shows filter suffixes and judge presence; link prompt requires a family to test
    the requirement's behaviour (a date-filter check had been linked to "seed spans multiple dates").
  - New failure review (`verdict/review.py`, one batched fast call, only when something failed): a
    requirement is FAILED only if the reviewer agrees the observations violate it; "does_not_violate"
    → unverified `link_disputed`.
  - Adversarial review (codex) fixes: per-assertion coverage, judge cannot clear interference, failure review
    fails closed, tighter transfer oracles (commit 92eaf7c).
- P7 done (2026-10-20): chaos variants, deterministic self-healing, bounded read/step recovery (no session
  re-entry, never a submit replay). Live acceptance: labels+testids and slow+flaky runs → 0 pass/fail flips vs
  baseline; details in `docs/P7_IMPLEMENTATION.md`.
  - Result on the reference run: 55 passed, 5 failed (zero, negative, over-balance amounts; date-range
    filter; inclusive end date — all confirmed on re-run), 7 unverified; one fail correctly disputed.
- P7J done (2026-10-20): Jev decision layer (`llm/typesafe.py`, `verdict/questions.py`; judge, review, link,
  heal on Jev with Gemini escalation; Jev priced in the cost meter). Live Jev-only run on the reference checks:
  25 passed, 4 failed (zero, negative, over-balance, date range; P(violates) 0.97–1.00), 38 unverified;
  0 false fails and every pass matches the reference. The rest is uncertain-by-design and escalates to Gemini.
  Cold-cache Jev cost ≈ $0.017 per run. Details and the design iterations in `docs/P7J_IMPLEMENTATION.md`.
  - Next with Gemini quota: `qa-agent verdict` with both keys to measure the escalated result.
- P8 done (2026-10-20): `qa-agent report --run --catalogue` (also run at the end of `qa-agent run`) writes a
  self-contained `report.html` (no network; data injected as escaped JSON, rendered with textContent only;
  failures first with PRD quote, expected vs observed, plain-word repro steps, review probability, screenshots;
  filterable requirement table; per-scenario steps, screenshots and API calls; cost by phase and provider),
  plus `report.md` and `report.json`. Steps are translated from locator patterns to plain words
  (`report/steps.py`); screenshots are downscaled JPEG (~6 MB for 49). Template designed by agy, reviewed and
  rendered headless at 1280 and 390 px with no errors or network requests. `verdict` and `run` save `cost.json`.
- P9 done (2026-10-21): dev-only evaluation harness (eval/harness.py, score.py, variants.json): shipped, fixed and
  seven single-defect mutants served as patched copies. Jev-only results: precision 0.97, recall 0.73, 0 false
  fails on the fixed build, mutant detection 3/7, 1 false pass (GEN-007) and 1 false fail (ACC-002). The probes and
  oracles catch every injected defect; all misses are Jev-only links in the 0.45-0.79 band, which escalate to Gemini
  by design. Details in docs/P9_IMPLEMENTATION.md. Review (codex, 8 findings) fixed in 03a8915.
  - Open: escalated (Gemini) eval run; jev-1.14.0 comparison (harness run --jev-models).
- P10 in progress (2026-10-21): architecture README (agent/README.md), walkthrough script
  (docs/VIDEO_SCRIPT.md), exported local report (reports/local-2026-10-21/: 38 passed, 5 failed, 11 unverified of
  54; all 5 failures trace to the zero/negative amount, overdraft and end-date filter defects; cold run $0.177).
  Cost report fixed: cached Jev and interpretation answers keep their usage, Gemini models are priced, and the
  report shows billed vs cold-run cost.
  - Note: `qa-agent run` re-interprets when the prompt or schema changed since the cached catalogue, so the
    catalogue can differ between runs (67 → 54 requirements here). Pin it for the hosted run by reusing the cache.
  - Open: hosted run once the URL arrives (copy the report into reports/), recording the video.
- Review 2026-10-21 (own audit + codex, 10 findings): judged failures could never be confirmed (always "flaky");
  eval ground truth used a gitignored catalogue guarded only by the PRD hash (now eval/catalogue.json, file hash);
  logout-refresh passed while still loading; positive-path transfers ignored the source balance; an unstated
  timezone was treated as UTC; zero history rows passed row checks; cost accounting dropped unknown usage,
  erased known costs next to an unpriced call and priced provider-cached input as free; a Gemini fallback that
  rejects the thinking level aborted escalated review; blocked vs non-reproduced re-runs were conflated. All fixed.
  - Two judge-input false positives found on a freshly seeded DB and fixed: the direction judge saw only the
    amount text, so transfers shown as "Moved between accounts" with "From X To Y" failed HIS-003 (it now also sees
    the account column); the rejection judge saw the field's always-visible format hint mixed into the error, so
    "Enter an amount to transfer." failed TRF-007 (hints read before submit are now subtracted). Fresh-DB run:
    exactly the 5 real defects fail (TRF-006, TRF-008, TRF-011, HIS-007, HIS-009).
  - Open: refresh reports/ once Gemini quota resets (that run had no link escalation: 36 unverified); reset the
    DB (`make reset`) before any exported run.
  - Added oracles: `general.external_requests` (every probe; the recorder now logs each request's method and
    origin, and a non-GET to a host outside the app fails, an outside GET stays unknown) and
    `general.out_of_scope_features` (gen.nav links vs the PRD's out-of-scope list). Both pass live locally; they
    are linked to GEN-007 / SEED-007 on the next full run.
