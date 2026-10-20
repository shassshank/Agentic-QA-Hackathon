# P6 — Verdicts: requirements × checks → passed / failed / unverified

Inputs: the requirement catalogue (`RequirementCatalogue`, `qa_agent/models.py`, produced by
`qa_agent/interpret`), `observations.json` (P4) and `checks.json` (P5, `qa_agent/verify`).
Output: `runs/<run>/verdicts.json` + console table, via `qa-agent run` (end to end) and
`qa-agent verdict --run runs/<run> --catalogue <path>` (from existing artifacts).

## 1. Link requirements to checks (`verdict/link.py`)

- One LLM call (`tier="planner"`, `thinking="low"`, `phase="link"`), schema-constrained, cached on disk
  under `.cache/link/<sha256(catalogue requirements json, check-family catalogue, model, prompt, schema)>.json`.
- Prompt input: each requirement (id, statement, assertions with oracle) and the **check families**:
  every distinct `behaviour` in checks.json with 2–3 example check ids and their `expected` text. Never
  send observed values or statuses (the link must not depend on outcomes).
- Output per requirement: `families` (list of behaviour tags, possibly with a suffix filter such as
  `transfer.amount_validation.zero` or `transfer.record` restricted to `.memo`), `coverage`
  (`full` | `partial` | `none`) and a one-line `rationale`. Unknown family names are dropped and recorded
  as warnings (deterministic validation, like `interpret.validate`).
- Deterministic guards: every requirement appears exactly once; requirements with testability
  `not_testable` get `families: []`.

## 2. Judge wording quality (`verdict/judge.py`)

- Collect checks with `needs_judge`. One batched call (`tier="fast"`, `thinking="minimal"`,
  `phase="judge"`) with items `{item_id, criterion, visible_text, context}` → `{item_id, verdict:
  pass|fail|unsure, rationale (≤ 25 words, must quote the text it relies on)}`. Cache by item content.
- Items over a token budget are split into several calls; total calls are metered by CostMeter.
- `unsure` keeps the check `unknown`. A judge result never overrides a deterministic fail.

## 3. Confirm failures (`verdict/confirm.py`)

- For every probe that produced a `fail` check, re-run that probe once in a fresh browser context
  (`plan.run_probes(..., only=[probe_id])` into `runs/<run>/confirm/`), re-evaluate it with
  `verify.run.evaluate`, and mark each originally failing check `confirmed` (still fails), `flaky`
  (passes or unknown on re-run) or `unconfirmed` (re-run blocked/errored).
- Only `confirmed` fails can make a requirement FAIL. `flaky` / `unconfirmed` → UNVERIFIED with reason.
- Confirmation is skippable (`--no-confirm`) for offline evaluation; then fails are reported as
  "unconfirmed" and requirements become UNVERIFIED, never FAIL.

## 4. Aggregate (`verdict/aggregate.py`)

Per requirement, over its linked checks (after judge + confirmation):
- **failed**: ≥ 1 confirmed fail. Include: requirement, steps to reproduce (from the failing probe's
  evidence steps, human-readable), expected, observed, evidence (screenshots, network entries).
- **passed**: coverage `full`, ≥ 1 linked check, every linked check `pass`.
- **unverified**: otherwise, with a reason from the closed set: `not_testable`, `no_linked_checks`,
  `partial_coverage`, `checks_unknown` (list them), `needs_judgement` (judge unsure or unavailable),
  `flaky`, `probe_blocked`, `concurrent_activity`, `llm_unavailable` (link call failed: every
  requirement unverified, run still completes).
- Report a coverage matrix: requirement → families → check counts by status.
- `verdicts.json`: `{run_tag, catalogue: {model, prompt_version, prd_sha256}, verdicts: [{requirement_id,
  area, title, status, reason, coverage, checks: [{id, status, confirmed}], failure: {...} | null}],
  summary, cost: CostMeter.by_phase(), warnings}`.

## Rules

- Same principles as P5: no hard-coded expected defects; deterministic everywhere except the two LLM
  calls; LLM failures degrade to UNVERIFIED, never crash, never PASS.
- Use the existing `LLMClient` protocol (`qa_agent/llm/base.py`) and `get_client`; tests use a fake
  client (see `tests/test_interpret.py::FakeClient`). No live LLM call in tests.
- Free-tier quota: total LLM calls for a full run must be ≤ 3 on a cold cache (interpret + link + judge),
  0 on a warm cache for unchanged inputs.

## Deliverables

- `qa_agent/verdict/{__init__,models,link,judge,confirm,aggregate,run}.py`
- CLI: `qa-agent verdict` (artifacts → verdicts) and `qa-agent run --prd --url [--no-confirm]`
  (interpret → probe → verify → link → judge → confirm → aggregate) in `cli.py`.
- `tests/test_verdict.py`: link validation (unknown family dropped, missing requirement), judge batching
  and caching, aggregation rules for each status/reason, confirmation classification, LLM failure →
  unverified.
