# P7 — Resilience: self-healing locators, recovery policies, chaos variants

Goal (plan): the agent keeps producing correct verdicts when the UI's labels change, test ids disappear, the
network is slow, or an API call fails transiently. Wrong verdicts are never acceptable; a step that cannot be
healed becomes `blocked` → requirement `unverified` with a reason.

## 1. Chaos variants (`qa_agent/chaos/`, test harness — the agent never reads it)

Injected into OUR browser only (no change to the target, no traffic beyond the app's own calls):

- `labels`: init script + MutationObserver that rewrites visible text and accessible names of controls using
  a fixed rename map kept in `chaos/variants.py` (e.g. "From account"→"Source account", "To account"→
  "Destination", "Amount (USD)"→"Transfer amount", "Memo"→"Note", "Transfer funds"→"Send money",
  "Apply filters"→"Search", "Clear filters"→"Reset", nav "Transactions"→"Activity", "Log out"→"Sign out",
  "Enter Demo"→"Start the demo"). Applies to `<label>`, button/link text, `aria-label`, option text is left
  alone (data).
- `testids`: strip every `data-testid` attribute continuously.
- `slow`: CDP latency 800 ms on every request.
- `flaky`: the first GET to each distinct API path returns 503 once (route handler), then passes through.
- Selected with `qa-agent probe --chaos labels,testids,slow,flaky` (any subset) and recorded in
  observations.json (`chaos: [...]`). Harness gets a `chaos` parameter; nothing else in the agent may
  import `chaos/variants.py` (enforce with a test that greps imports).

## 2. Self-healing locators (`qa_agent/recover/heal.py`)

Hook: `ScenarioRun._visible` (used by click/fill/select/submit). When the preferred locator is not visible
within the timeout:
1. Snapshot interactive elements (role, accessible name, label text, placeholder, name/id attributes,
   visible text, nearby heading) via one `page.evaluate`.
2. Deterministic match first: same role; score = token overlap between the wanted pattern's words and the
   candidate's name/label (case-folded, stemmed lightly), plus a small generic synonym table that is NOT
   derived from the chaos map (e.g. from↔source, to↔destination, amount↔sum, memo↔note↔description,
   apply↔search↔filter, clear↔reset, log out↔sign out↔exit, transactions↔history↔activity,
   enter↔start↔begin). Accept only a unique best candidate above a threshold.
3. Otherwise one LLM call (`tier="fast"`, `phase="heal"`, schema `{index | null, confidence, reason}`) with
   the intent (action + wanted label pattern + what the step is for) and the snapshot list; cached on disk
   keyed by (intent, snapshot digest, model, prompt).
4. Use the healed element; remember intent→accessible name in a per-run locator cache so later steps and
   probes reuse it without new calls.
5. Record a `heal` step in evidence: original target, healed target, method (deterministic|llm), score.
   If no candidate: raise ActionError as today (probe → blocked).

Readers (`observe/pages.py`) already fall back from test ids to structure; extend where a test id is the only
hook (e.g. result count, transfer side balances) so `testids` chaos does not blind them.

## 3. Recovery policies (`qa_agent/recover/policy.py`)

- Session lost (landed on the landing page or 401 seen while a protected view was expected) during a probe
  that is not testing access: re-enter the demo once and retry the step.
- Transient API failure surfaced as an error state with a retry control during a non-error probe: press retry
  once (bounded), then continue; record it. Probes that test error handling (`gen.recoverable_error`) opt out.
- Bounded step retry with backoff for ActionError caused by detached/animating elements (max 2).
- Every recovery is a step in evidence; a probe that needed recovery is still `completed`, and its checks are
  evaluated normally (recovery must never change what is being asserted, e.g. never retry a transfer submit).

## 4. Acceptance

- `qa-agent probe --chaos labels,testids` and `--chaos slow,flaky` on the local app: every probe completes or
  is blocked with a reason; no probe errors.
- `qa-agent verify` on those runs: no check flips from fail→pass or pass→fail relative to the baseline run
  except to `unknown`; report the diff (`qa_agent/chaos/compare.py`, CLI `qa-agent chaos-compare --base
  runs/A --variant runs/B`).
- Heal LLM calls ≤ 10 on a cold cache for the labels variant, 0 on a warm cache.
- Unit tests: deterministic matcher (positive/negative/ambiguous), heal cache, policy guards (never retries a
  submit), chaos import isolation.
