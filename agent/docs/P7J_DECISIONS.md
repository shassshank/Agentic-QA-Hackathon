# P7J — Jev decision layer

Goal: move the agent's small typed decisions (wording judge, failure review, requirement→check linking,
locator-heal fallback) from Gemini to TypeSafe Jev, keep Gemini as the escalation path for uncertain answers,
and keep today's fail-closed semantics: anything still uncertain stays unknown / unverified, never a guess.
Background and trial data: `docs/PLAN.md` §4 "Decision layer (Jev)".

## API facts (docs.typesafe.ai, checked 2026-10-20)

- `POST https://api.typesafe.ai/v1/systemone`, header `Authorization: Bearer $TYPESAFE_API_KEY`.
- Body: `{"model": "jev-1.13.0", "state": <str|object|array>, "questions": {<name>: question}}`.
  - noul: `{"type":"noul","instructions":..., "criteria": {"true":..., "false":...}?}` → `{"type":"noul","noul":p}`
  - choice: `{"type":"choice","instructions":..., "criteria": {option: description|null, ...}}` (1–255 options)
    → `{"type":"choice","choice":opt,"confidence":c,"probabilities":{opt:p}}`
- Response: `{"model":..., "answers": {name: answer}, "usage": {"input_tokens":n,"output_tokens":m}}`.
- No batch/items field: one request = one state + many questions. Parallelise requests client side.
- Limits: 64k tokens per request (state + all questions), 32k per question (state + that question);
  body ≤ 32 MiB; nesting ≤ 64. 400 `max_tokens_exceeded` when too long; 422 validation detail array.
- Retry 429 and 529 with exponential backoff. Other 4xx are permanent.
- Price: $0.042 per million input tokens, output free. Question names are not sent to the model, so every
  instruction must carry its full meaning; reference state fields with backticked paths (`item.observed`).

## 1. `qa_agent/llm/typesafe.py` — `DecisionClient`

- `class DecisionUnavailable(RuntimeError)`.
- `DecisionClient(api_key, *, model='jev-1.13.0', meter, timeout_s=20, max_workers=8, retries=3,
  transport=None)`; attribute `provider = 'typesafe'`. Use `httpx` (already a dependency); `transport` lets
  tests inject `httpx.MockTransport`. Never log or include the key in exceptions or repr.
- `ask(state, questions: dict, *, phase: str) -> dict` (answers by name) — one request; records one
  `CostEntry(phase=phase, model=<response model>, input_tokens, output_tokens, latency_ms)` on the meter.
  Retries 429/529/timeouts/connection errors with backoff (0.5 s, 1 s, 2 s, honour `Retry-After` up to 10 s);
  then raises `DecisionUnavailable`. Permanent 4xx raise `DecisionUnavailable` with status + short detail.
- `ask_many(requests: list[tuple[state, questions]], *, phase) -> list[dict | DecisionUnavailable]` —
  thread pool, order preserved, per-request failures returned not raised.
- Validate answer shape (type matches, noul in [0,1], choice in criteria); a malformed answer is
  `DecisionUnavailable` for that request.
- `config.py`: `typesafe_api_key: SecretStr|None` (`TYPESAFE_API_KEY`), `typesafe_model` (`TYPESAFE_MODEL`,
  default `jev-1.13.0`). `llm/__init__.py`: `get_decider(settings, meter) -> DecisionClient | None` (None when
  no key). Meter default prices: `{'jev-1.13.0': (0.042, 0.0), 'jev-1.14.0': (0.042, 0.0)}` merged into
  `CostMeter` defaults (keep other models unpriced; do not invent Gemini prices).
- Cache: disk cache under `.cache/decide/<phase>/<sha256>.json` keyed by `key_for(model, state, questions)`
  (reuse `verdict.link.key_for`). Cache hits record a `CostEntry(cache_hit=True)` with zero tokens.
  Never cache a failure.

## 2. Wording judge (`verdict/judge.py`)

New function `judge_checks_jev(checks, decider, client, *, cache_dir)`, same eligibility rule as today
(`needs_judge`, status unknown, reason starts with "wording"). Questions come from `verdict/questions.py`:

- A small, generic question bank keyed by the check's id pattern / criterion wording, never by app strings
  or known defects. Initial entries:
  - criterion mentions explanation / rejection / error / failure (e.g. "clear explanation of rejection"):
    noul `explains`: "A form submission was rejected. Does the visible text in `item.visible_text` tell the
    user what is wrong or what to do to fix it (a missing field, an invalid value, or a broken rule)? Generic
    text such as 'Error' or 'Something went wrong' does not."
  - criterion mentions direction / credit / debit (amount direction): noul `direction_shown`: "Look only at
    `item.visible_text.amount_text`. Does that string by itself show whether money came in or went out, by a
    sign (+, -, −) or words such as in/out/credit/debit? A bare amount like '$10.00' does not."
    Plus a deterministic code rule in the same module: if the string has an explicit sign or in/out word that
    contradicts `type` (deposit shown as out, withdrawal shown as in) → fail regardless of Jev.
  - fallback for any other judged check: noul `meets`: "Does the visible text in `item.visible_text`
    satisfy this criterion: `item.criterion`? Judge only the visible text."
- State: `{"item": {"criterion", "visible_text", "context"}}` from the existing `judge_input`.
- Decision: p ≥ 0.85 → pass; p ≤ 0.15 → fail; otherwise escalate. Thresholds are module constants.
- Escalation: collect uncertain checks and send only those through the existing Gemini `judge_checks`
  (unchanged, quote-checked) when `client` is not None; else they stay unknown with reason
  `"wording: jev uncertain (p=0.62); no escalation"`.
- Evidence: set `check.reason` to e.g. `"jev noul explains p=0.93 (jev-1.13.0)"` and store
  `check.judge_input['decision'] = {"provider":"typesafe","question":name,"p":p,"model":...}` (or an
  equivalent field on the check) so the report can show why.
- `DecisionUnavailable` for an item → treat as uncertain (escalate or keep unknown) and add a warning.

## 3. Failure review (`verdict/review.py`)

New `review_failures_jev(verdicts, catalogue, decider, client, *, cache_dir)`; build the same `item` dict as
today. One request per failed requirement: state `{"item": item}`, one choice question `linkage`:
instructions "Taking `item.failing_observations` as true, do they violate the requirement in
`item.requirement` (with `item.assertions`)? They violate it only if they contradict what the requirement
states; observations about a different behaviour do not." Criteria: `violates`, `does_not_violate`
("the observations concern a different behaviour or do not contradict the requirement"), `unsure`.

- `violates` with p ≥ 0.90 → stays failed; `does_not_violate` with p ≥ 0.90 → unverified/`link_disputed`.
- Otherwise escalate those requirements to the existing Gemini `review_failures` when `client` exists; if no
  client → unverified/`unreviewed`. `verdict.review` stores `{"verdict","p","provider","model","escalated"}`.
- Unavailable → escalate, else `unreviewed` (unchanged semantics).

## 4. Linking (`verdict/link.py`)

New `link_requirements_jev(catalogue, checks, decider, client, *, cache_dir) -> LinkResult`.

- Options = every family tag from `family_catalogue(checks)` plus, for families with `has_judge`, the
  `family.suffix` tags for their judge suffixes, plus `none`. If > 254, fall back to the Gemini link.
  Each option's description = the family's example `expected` strings (outcome-free, ids only, as today)
  truncated to ~160 chars.
- One request per testable requirement; state `{"requirement": {"id","title","statement","assertions"}}`;
  one choice question per assertion `a<i>`: "Which check family tests assertion `requirement.assertions[i]`
  of this requirement? Pick the family whose checks test the stated behaviour, not one that merely touches
  the same data; pick none if no family tests it." Keep within the 32k-per-question budget (estimate
  chars/4; if over, drop examples beyond the first).
- Accept: top option not `none` and its p ≥ 0.40 → that family, plus any other non-none option with
  p ≥ 0.25 (multi-family). Top `none` with p ≥ 0.60 → assertion unmapped. Anything else → uncertain.
- Requirements with any uncertain assertion: if `client` exists, link only those requirements with the
  existing Gemini `link_requirements` (subset catalogue) and merge; otherwise their uncertain assertions stay
  unmapped (coverage computed by `validate` → partial/none). Not-testable requirements: no request.
- Always pass the merged result through `validate`. Cache per requirement (decider cache). Rationale field:
  `"jev choice"` with top probabilities.

## 5. Heal fallback (`recover/heal.py`)

`Healer(cache_dir, client=None, decider=None)`. After deterministic match fails and `decider` exists: one
choice over eligible candidates (`c<index>` → `role: name`) plus `none`; accept p ≥ 0.85 and not none,
method `'jev'`. Else existing LLM path, else blocked. Do not wire into the CLI probe command (unchanged
scope) unless trivial.

## 6. Orchestration (`verdict/run.py`, `cli.py`)

- `run_verdict(..., client=None, decider=None, ...)`. When `decider` is present use the Jev functions with
  `client` (may be None) as escalation; when absent keep today's Gemini path unchanged.
- `unavailable` (all requirements `llm_unavailable`) only when linking produced nothing from either path.
- CLI `verdict` and `run`: build `decider = get_decider(settings, meter)`; Gemini client optional when a
  decider exists (catch its errors, print once). Hidden flag `--decider jev|none` (default jev when a key
  exists). `run` still needs Gemini for interpretation unless the interpret cache hits; on a missing Gemini
  key with a cached catalogue it must still proceed.
- Cost: `verdicts.json` `cost` keeps `meter.by_phase()`; phases `judge`, `review`, `link` now show Jev
  tokens priced; Gemini escalations appear as their own phase names (`judge_escalation`, etc. — pass
  `phase=` accordingly) so the split is visible.

## 7. Tests (`tests/test_decide.py`), fake transport / fake decider only, no network

- Client: request shape and auth header (key never in repr/errors); retry on 429/529 then success; permanent
  422 → unavailable; malformed answer → unavailable; meter entry recorded; cache hit records cache_hit.
- Judge: pass/fail/uncertain thresholds; uncertain escalates to a fake Gemini client; no client → unknown with
  reason; sign-contradiction rule fails a deposit shown as "−$950.00" even if Jev says yes; non-wording
  unknown checks untouched.
- Review: violates≥.9 stays failed; does_not_violate≥.9 → link_disputed; low confidence escalates; no client
  → unreviewed; outage → unreviewed.
- Link: multi-family acceptance, none → unmapped, uncertain → Gemini subset merge, > 254 options → Gemini;
  coverage still from `validate`.
- Heal: Jev choice accepted at ≥ .85, `none` blocks.
- Existing 229 tests stay green.

## Constraints

- Do not touch probes, oracles, the target app or `agent/.dev/`. No commits. No live network calls in tests.
- Questions must stay generic (derived from criterion wording and family examples); never encode DemoBank
  strings, known defects or `eval/known_issues.yaml` content.
- Report in `docs/P7J_IMPLEMENTATION.md`: files changed, decisions taken where this spec was silent, test
  results.
