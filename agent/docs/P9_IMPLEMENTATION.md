# P9 implementation

Files: `eval/variants.json`, `eval/harness.py`, `eval/score.py`, `tests/test_eval.py`.
The tooling uses only the standard library. Ground truth is never passed to the
agent. The catalogue SHA is checked before preparation and scoring.

Decisions:
- Mutants inherit shipped patches and expected failures. Their detection metric
  uses only their added must-fail requirements. CORS is inherited once; fixed has
  its own CORS patch. `--frontend-port` rewrites that patch's origin.
- Fixed validates positive amounts at the API boundary and checks available funds
  under the repository's existing row locks, returning the existing error envelope.
  End dates use next-day Pacific midnight, including across daylight saving changes.
- The source stores masked identifiers only. Mask/email mutants expose synthetic
  full values in the relevant UI views; they do not invent database columns.
- The same-account mutant removes both rejection gates and handles the duplicate
  transaction-entry constraint with ON CONFLICT, allowing the request to complete.
- Preparation excludes `.env` as well as build/dependency artifacts. Backend env
  is loaded privately into its child environment from the original backend `.env`;
  simple KEY=value entries, optional export and quoted values are supported.
  Dotenv interpolation and multiline values are not supported.
- Applied manifests contain exact match counts and patched-file hashes. Serve checks
  those hashes and verifies the backend package import resolves into the copy.
- Existing PID manifests block competing serves and database resets. This is a local,
  sequential harness, not a concurrent scheduler. Stop sends TERM then KILL to groups.
- Model reruns use a snapshot of the original checks, observations and evidence,
  preserved before the first verdict's confirmation reruns. Model identifiers must
  be safe directory names (no slash). Reports are generated for every model.
- Missing expected verdicts are listed separately, not counted as honest unverified.
  Probe runtime is the sum of recorded probe durations; absent cost remains null.
  Metrics JSON contains coverage, review probabilities and per-requirement rows;
  Markdown contains the overall table and aggregate/model comparison metrics.
  Overall micro-averages include every scored run; model aggregates are separate.

Validation from `agent/`:

```sh
UV_CACHE_DIR=/tmp/p9-uv uv run pytest -q tests/test_eval.py
# 16 passed
UV_CACHE_DIR=/tmp/p9-uv uv run pytest -q tests/test_eval.py -k every_variant_patch_count
# 9 passed, 7 deselected
UV_CACHE_DIR=/tmp/p9-uv uv run pytest -q
# 302 passed, 3 skipped
```

The parametrized patch-count test copies the real backend and frontend/src without
node_modules, applies every variant's complete patch chain, checks exact counts,
and parses patched Python files. No servers, Docker, browser or harness run were
started. Live startup, database and browser behavior require validation outside
this sandbox. No changes were made to the target app, qa_agent behavior or .dev.

Exact run commands (outside the sandbox, from `agent/`, with the original backend
venv, frontend node_modules and database prerequisites installed):

```sh
uv run python eval/harness.py run shipped
uv run python eval/harness.py run fixed
uv run python eval/harness.py run m-mask
uv run python eval/harness.py run m-logout
uv run python eval/harness.py run m-typefilter
uv run python eval/harness.py run m-sameacct
uv run python eval/harness.py run m-email
uv run python eval/harness.py run m-decimals
uv run python eval/harness.py run m-dupsubmit
uv run python eval/harness.py score
```

Individual lifecycle and model comparison examples:

```sh
uv run python eval/harness.py reset-db
uv run python eval/harness.py prepare shipped --frontend-port 5273
uv run python eval/harness.py serve shipped --backend-port 8100 --frontend-port 5273
uv run python eval/harness.py stop shipped
uv run python eval/harness.py run fixed --jev-models jev-1.13.0,jev-1.14.0
uv run python eval/harness.py score --variants shipped fixed
```

`run` resets the shared database before preparation and always stops its servers
in a finally block. The baseline app may remain on 8000/5173. Outputs live under
`runs/eval/`, including process logs, manifests, run artifacts and metrics.

## Live results (2026-10-21, Jev only, after the review fixes)

All nine variants ran end to end (patched copies on ports 8100/5273, shared DB reset before each, Gemini disabled
so nothing escalates). Catalogue: eval/catalogue.json (67 requirements, pinned by file hash).

| Variant | TP | False fail | False pass | Honest unverified | Precision | Recall | Own defect detected |
|---|---:|---:|---:|---:|---:|---:|---|
| shipped | 4 | 0 | 0 | 1 | 1.00 | 0.80 | yes (TRF-008/009/015, HIS-008; HIS-010 unverified) |
| fixed | 0 | 0 | 0 | 0 | — | — | — (0 failures once the defects are fixed) |
| m-logout | 6 | 0 | 0 | 1 | 1.00 | 0.86 | yes (SES-005, SES-007) |
| m-typefilter | 5 | 0 | 0 | 1 | 1.00 | 0.83 | yes (HIS-006) |
| m-sameacct | 5 | 0 | 0 | 1 | 1.00 | 0.83 | yes (TRF-005) |
| m-email | 4 | 0 | 0 | 2 | 1.00 | 0.67 | no — PRO-001 unlinked (unverified) |
| m-decimals | 4 | 0 | 0 | 2 | 1.00 | 0.67 | no — ACC-005 unlinked (unverified) |
| m-mask | 4 | 1 | 0 | 3 | 0.80 | 0.57 | no — caught by accounts.masking.page_digits but blamed on ACC-002 |
| m-dupsubmit | 4 | 0 | 1 | 1 | 1.00 | 0.67 | no — duplicate_submit checks failed, GEN-007 linked only to loading_state |

Overall (micro): precision 0.97, recall 0.73, mutant detection 3/7. Cost per variant (Jev) is a few tenths of a
cent; runtime about 5 minutes per variant.

What the numbers say:
- The oracles and probes catch every injected defect: each mutant produced at least one failing check that names
  it. Every miss is in linking a requirement to those checks without Gemini (Jev link probability in the 0.45-0.79
  band: duplicate_submit 0.45 for GEN-007, accounts.masking 0.53/0.68 for ACC-003/006, profile 0.70 for PRO-001).
  That band escalates to Gemini by design; these figures are the Jev-only floor.
- One false pass (GEN-007) and one false fail (ACC-002, masking defect blamed on the account-fields family). Both
  come from family-level Jev links. Thresholds were not retuned on these variants to avoid fitting the test set.
- Ground-truth corrections, made before scoring and recorded in variants.json: the first logout mutant only skipped
  server-side revocation, which a browser cannot observe because the cookie is still deleted (the agent was right to
  pass); the mutant now also keeps the cookie. m-decimals lists TRF-013/017/020 as allowed knock-on failures (a
  0.01 transfer shows no visible balance change without cents).

Bugs found by these runs and fixed: the double-submit probe fed transfer atomicity, record and success-timing
checks (three applied transfers failed TRF-013/017/020 instead of only the duplicate-submit family); harness stop
and port checks on macOS. Next: the same runs with Gemini escalation once its quota allows, to measure the
escalated recall.
