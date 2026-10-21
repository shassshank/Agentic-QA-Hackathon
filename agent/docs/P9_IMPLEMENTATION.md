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
