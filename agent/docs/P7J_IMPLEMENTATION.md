# P7J implementation

Implemented the Jev decision layer described in P7J_DECISIONS.md. Without a decider, the existing Gemini judge, review and link functions retain their prompts, caches and behavior.

## Files changed

- `qa_agent/llm/typesafe.py`: httpx DecisionClient, DecisionUnavailable, ordered concurrent requests, response validation, bounded retries, disk caching, token accounting and escalation phase adapter.
- `qa_agent/llm/__init__.py`: optional get_decider factory.
- `qa_agent/llm/meter.py`: default Jev input/output prices for 1.13.0 and 1.14.0; Gemini remains unpriced.
- `qa_agent/config.py`: secret TypeSafe key and configurable model from environment.
- `qa_agent/verdict/questions.py`: generic wording questions, thresholds, direction contradiction rule and failure-linkage question.
- `qa_agent/verdict/judge.py`: per-check Jev decisions, evidence metadata, deterministic direction rejection and uncertain-only Gemini escalation.
- `qa_agent/verdict/review.py`: per-requirement Jev failure review and conservative escalation/unreviewed handling.
- `qa_agent/verdict/link.py`: outcome-free family choices, per-assertion mapping, multi-family acceptance, subset escalation and final validate pass.
- `qa_agent/verdict/run.py`: optional decider orchestration with existing confirmation and aggregation.
- `qa_agent/recover/heal.py`: optional Jev choice fallback between deterministic matching and existing Gemini healing.
- `qa_agent/cli.py`: hidden --decider jev|none for verdict/run and cached interpretation support without a Gemini client.
- `tests/test_decide.py`: offline client, judge, review, linking, healing, orchestration and CLI tests.
- `docs/P7J_IMPLEMENTATION.md`: this implementation record.

No probes, oracles, target application or .dev files changed. No commits made.

## Decisions where the spec was silent

- `retries=3` means three retries after the initial request. Retry-After supports seconds and HTTP dates, caps the provider delay at ten seconds, and never shortens exponential backoff.
- DecisionClient defaults to `.cache`; phase wrappers use their supplied cache_dir as the root, yielding `<cache_dir>/decide/<phase>/<hash>.json`. Cache read corruption is a miss; cache write errors do not discard a valid decision. Responses retain their actual model for evidence and cache accounting.
- Choice confidence drives review/healing thresholds; linking uses the returned per-option probabilities. Unknown choices, invalid probabilities and missing requested answers fail the whole request.
- Direction contradictions are rejected before requesting Jev; a model answer cannot override the explicit contradiction.
- More than 254 linking options without an escalation client is unavailable. All request failures without a successful escalation are unavailable; a valid `none` or uncertain answer remains an ordinary unmapped link.
- Requirements with uncertain assertions are replaced by the Gemini subset result when escalation succeeds. If escalation fails, accepted Jev mappings survive and uncertain assertions remain unmapped.
- Escalations reuse existing Gemini prompt/cache behavior through a phase adapter, recording judge_escalation, review_escalation and link_escalation on actual calls.
- CLI cached interpretation uses the existing interpreter's exact cache-key calculation through a client exposing the configured provider/model; on a cache miss it raises a clear missing-interpreter error. No alternate cache scan or stale catalogue selection is used.
- Probe CLI healing wiring remains unchanged, as permitted by the spec.

## Validation

`UV_CACHE_DIR=/tmp/p7j-uv uv run pytest -q`

**259 passed, 3 skipped in 0.68s.** Browser tests skipped in this environment. New tests use only httpx.MockTransport and fake deciders/clients; no live provider calls.

`git diff --check` passed.

## Review fixes and live validation (2026-10-20, run outside the sandbox)

Live Jev-only runs (`GEMINI_API_KEY=` so nothing escalates) on copies of `runs/p5-base` (67 requirements,
41 probes, confirmation re-runs live). Reference: the earlier dev verdicts (55 passed / 5 failed / 7 unverified).

Changes made after the first live run:
- Review and heal thresholds use the chosen option's probability, not Jev `confidence` (distribution concentration).
- Linking is two-stage. A single 57-way choice spread probability thin (correct families at 0.23–0.37). Stage 1
  (choice) now only ranks; stage 2 asks one noul per top-3 family (plus its judged wording groups), one request
  per (assertion, candidate), with the family's scenario titles and checks as **state** (evidence) instead of
  question text. Group-level (single suffix) candidates scored 0.6–0.7 because one group covers only part of an
  assertion, so stage 2 verifies whole families (families are already per case, e.g. `amount_validation.zero`).
  Scenario titles (our own probe titles from `observations.json`) moved correct families from ~0.6 to 0.8–0.95.
- Accept bar stays 0.80. The 0.55–0.79 band mixes right and wrong links (e.g. `session.enter` 0.66 for a logout
  requirement), so it escalates to Gemini, or stays unmapped without it. No relaxed rule was tuned in-sample.
- Review choice gained `condition_not_met` competing with `violates`: a conditional requirement ("if a transfer is
  rejected, explain it") was failed at 0.99 when the transfer had actually been accepted. A separate premise noul
  was rejected (it scored a real unconditional defect 0.86).

| Run | Passed | Failed | Unverified | Notes |
| --- | --- | --- | --- | --- |
| first (single choice link) | 49 | 5 | 13 | 7 requirements unlinked at low probability |
| group-level stage 2 | 8 | 1 | 58 | partial-coverage groups never reach 0.80 |
| family-level, checks in question text | 13 | 3 | 51 | same question as the trial scored lower when inlined |
| family-level, checks + scenarios as state | 25 | 6 | 36 | TRF-021 false fail (conditional premise) |
| **final: + condition_not_met review option** | **25** | **4** | **38** | 0 false fails, 0 new passes vs reference |

Final run: failed TRF-008, TRF-009, TRF-015, HIS-008 (Jev review P(violates) 0.97–1.00), all known defects.
TRF-012 and TRF-021 are `unreviewed` (P(violates) below 0.90) pending Gemini; HIS-010 is unlinked (0.77);
TRF-007/TRF-012 wording check stays unknown (Jev 0.5–0.85 band). Every pass matches a reference pass.

Cost (cold cache, Jev only): link $0.0156 (≈280 requests, two stages), judge $0.0006 (33), review $0.0004 (6) —
about $0.017 per run, $0 on a warm cache. With Gemini available only the ~34 uncertain requirements go to one
Gemini link call, plus rare judge/review escalations, so a full run fits the free-tier daily quota.

Tests: 267 passed (includes the 3 live browser tests).
