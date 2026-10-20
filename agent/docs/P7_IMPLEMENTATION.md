# P7 implementation and validation

Implemented browser-only chaos variants; conservative snapshot-based locator healing with generic synonyms,
per-run name reuse, injectable fast LLM fallback and disk cache; evidence method/score fields; guarded read
recovery and bounded detached/stability retries; observer fallbacks; discovered navigation content checks;
and the `chaos-compare` command.

## Files

Modified:
- `qa_agent/cli.py`
- `qa_agent/execute/browser.py`
- `qa_agent/execute/evidence.py`
- `qa_agent/observe/pages.py`
- `qa_agent/plan/probes.py`
- `qa_agent/plan/registry.py`
- `qa_agent/verify/util.py`

Added:
- `qa_agent/recover/{__init__,heal,policy}.py`
- `qa_agent/chaos/{__init__,variants,compare}.py`
- `tests/test_resilience.py`
- this report

The supplied `docs/P7_RESILIENCE.md` was read and left unchanged. Concurrent changes in verdict modules and
other tests were left untouched. No commits, branch changes, target modifications or live LLM calls were made.

## Validation limits

Commands use `UV_CACHE_DIR=/tmp/p7-uv uv run` because the default uv cache is outside the writable sandbox.

The requested baseline was attempted before implementation. Chromium exits before any scenario starts:
`bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer…: Permission denied (1100)`.
Both requested chaos runs fail at the same browser startup boundary. This execution environment also cannot
reach the localhost target in the existing browser test availability check. Permission escalation is disabled.

Baseline and variant `verify` commands were attempted, followed by both `chaos-compare` commands. They cannot
produce reports because startup failure leaves no observations/checks. Diagnostic logs are retained under
`runs/p7-validation/`. No empty observations or synthetic acceptance results were substituted.

| Variant | Deterministic heals | LLM-needed heals | Probe outcome | Comparison |
| --- | --- | --- | --- | --- |
| p7-base | unavailable | unavailable | browser startup blocked | unavailable |
| p7-labels (labels,testids) | unavailable | unavailable | browser startup blocked | unavailable |
| p7-net (slow,flaky) | unavailable | unavailable | browser startup blocked | unavailable |

No individual probe ran, so there are no probe-specific blocked reasons or verified flip counts. Live LLM
calls were zero. Cold/warm cache behavior is covered with a fake client; the live labels call budget remains
unmeasured.

P7 tests cover positive/negative/ambiguous matching, role mismatch, cache reuse and snapshot invalidation,
abstention/confidence/index guards, no-client behavior, recovery exclusions, no submit replay, retry limits
and backoff, cross-path session bounds, chaos import isolation, discovered paths and check comparisons.
The P7-focused suite passed with existing observer/oracle tests: 168 passed, 3 browser tests skipped.
The latest full suite passed: **226 passed, 3 skipped**. The skipped tests require the localhost browser
integration target. Earlier runs saw failures while concurrent verdict/oracle edits were in progress;
those failures no longer occur in the latest run. See `p7-tests-final.log`.
`git diff --check` passed.

## Interpretations

- The spec does not define a match threshold or LLM confidence floor: use Jaccard token overlap >= .45,
  reject tied best scores, and require LLM confidence >= .85 plus an eligible candidate index.
- Recovery is disabled for every session/access probe as well as `gen.loading` and `gen.recoverable_error`.
  This conservatively preserves access assertions. A session re-entry is allowed once per probe session;
  transient read retry is allowed once per requested view. A submit is never replayed.
- The optional LLM is injected through `Healer`; the probe CLI constructs no live provider. No-client
  ambiguity blocks with a reason. This honors the instruction to test the LLM path only with a fake.
- Missing checks compare as unknown, identified by `(probe_id, check_id)`. Comparison reports both
  pass-to-fail and fail-to-pass changes.

Acceptance remains incomplete until the browser/target runs establish probe outcomes and compare results.

## Self-evaluation

Using the ECC agent-self-evaluation rubric: accuracy 4/5 (unit tests pass, browser integration unmeasured),
completeness 2/5 (live acceptance cannot run), clarity 4/5 (explicit commands and diagnostics),
actionability 4/5 (implementation and rerun commands available), conciseness 4/5 (report retains necessary
validation detail). Overall 3.6/5. The highest-impact remaining step is rerunning the three browser variants
in an environment with Chromium and localhost access, then inspecting check flips and heal counts. The
user would reasonably regard the implementation as awaiting acceptance, not as a completed P7 phase.

## Live validation (2026-10-20, run outside the sandbox) — supersedes "Validation limits" above

Fresh DB reset, then `qa-agent probe` → `verify` → `chaos-compare` against `runs/p7b-base`:

| Variant | Probes | Heals | Recoveries | Compare vs base |
| --- | --- | --- | --- | --- |
| p7b-base | 41 completed, 15/15 restored | 0 | 0 | — |
| p7b-labels (labels,testids) | 41 completed, 15/15 restored | 42 deterministic (3 distinct controls), 0 LLM | 0 | 0 flips; 3 pass→unknown, 3 unknown→pass (renamed nav labels routed to judge) |
| p7b-net (slow,flaky) | 40 completed, 1 blocked, 15/15 restored | 0 | 0 | 0 flips; 24 pass→unknown (blocked probe) |

- Blocked: `trf.slow_submit` under network chaos — its own 1.5 s latency on top of the 800 ms chaos latency pushed
  the transfer page load past the 10 s timeout; blocked with reason, its checks unknown, balances restored.
- Most controls survived renaming without healing because probes locate by label patterns
  (`from|source`, `transfer|submit|send`); healing covered "Enter Demo" and the account-card links.
- Review fixes applied before acceptance: session re-entry recovery removed (it masked the access check and could
  hide a logout-on-refresh defect), so the "session re-entry" interpretation above no longer holds; recovery is off
  unless a probe is named; recoveries/heals are recorded in facts; duplicate-submit/slow probes no longer feed
  `transfer.valid` (their extra clicks met the cleared form's validation — a flaky false fail found by the
  compare); the memo-optional form check accepts note/reference labels; history judge check ids are keyed by
  transaction id; previously healed intents probe the original locator briefly.
- Not wired: the LLM heal fallback in the CLI (deterministic matching sufficed; 0 LLM calls).
