# P9 — Evaluation harness

Goal: measure the agent's accuracy (precision, recall, false passes, honest unverified) on the shipped DemoBank
build, a build with the known defects fixed, and single-defect mutants. Dev-only tooling: it lives in
`agent/eval/`, is never imported by `qa_agent`, and never changes `test-website/` (variants are patched copies).
Ground truth is dev-only and must never be read by the agent at runtime.

## Layout

- `agent/eval/variants.json` — variant definitions and ground truth (below). JSON, no new dependency.
- `agent/eval/harness.py` — CLI (`uv run python eval/harness.py <command>`), stdlib only.
- `agent/eval/score.py` — pure scoring functions (unit-tested).
- `agent/tests/test_eval.py` — unit tests for patching and scoring only (no servers, no network).
- Outputs under `agent/runs/eval/` (gitignored via `runs/`).

## Variant definition (`variants.json`)

```json
{
  "catalogue_sha": "<prd_sha256 of eval/catalogue.json>", "catalogue_file_sha256": "<sha256 of eval/catalogue.json>",
  "variants": [
    {"name": "shipped", "description": "unmodified build", "patches": [],
     "must_fail": ["TRF-008", "TRF-009", "TRF-015", "HIS-008", "HIS-010"],
     "may_fail": ["TRF-012", "TRF-021"]},
    {"name": "fixed", "description": "known defects KI-01..04 fixed", "patches": [ ... ],
     "must_fail": [], "may_fail": [], "now_correct": ["TRF-008", "TRF-009", "TRF-015", "HIS-008", "HIS-010"]},
    {"name": "m-mask", "base": "shipped", "patches": [ ... ], "must_fail": ["ACC-003", "ACC-006"], "may_fail": ["PRO-002"]}
  ]
}
```

A patch is `{"file": "backend/demobank/main.py", "find": "<exact text>", "replace": "<text>", "count": 1}`, applied to
the copy. `base` means the base variant's patches are applied first and its `must_fail`/`may_fail` are inherited
(mutants sit on the shipped build). Every `find` must match exactly `count` times or preparation fails loudly — no
silent no-op mutants.

Mutants to define (read the target source in `test-website/` to write minimal, realistic patches; one defect each):

| Name | Injected defect | must_fail | may_fail |
|---|---|---|---|
| fixed | API rejects zero, negative and over-balance amounts with the app's existing rejection error shape; date filter end date inclusive (whole local day) | — | — |
| m-mask | dashboard/account views show the full account identifier instead of the masked one | ACC-003, ACC-006 | PRO-002 |
| m-logout | logout returns success but does not invalidate the server session | SES-005, SES-007 | SES-004 |
| m-typefilter | history API ignores the type filter | HIS-006 | HIS-009 |
| m-sameacct | API accepts a transfer whose source equals destination | TRF-005 | TRF-006, TRF-021 |
| m-email | profile shows the full email address | PRO-001 | PRO-002 |
| m-decimals | dashboard balances render without cents (whole dollars) | ACC-005 | ACC-009 |
| m-dupsubmit | transfer submit button is not disabled while the request is pending (and the client does not dedupe) | GEN-007 | TRF-018, GEN-006 |

Every variant (including shipped) also gets the harness patch that adds its frontend origin to the backend CORS
`allow_origins` list (it is hard-coded to `http://localhost:5173`).

## Commands

- `prepare <variant>` — copy `test-website/backend` and `test-website/frontend` (excluding `node_modules`, `.venv`,
  `__pycache__`, `dist`, `*.egg-info`) to `runs/eval/<variant>/app/`, symlink `frontend/node_modules` to the original,
  apply patches, write `applied.json` (each patch, match count). Recreate the copy each time.
- `serve <variant>` — start the patched backend with the original backend venv
  (`test-website/backend/.venv/bin/python -m uvicorn demobank.main:app --host 127.0.0.1 --port 8100`, cwd = copied
  backend so the patched package is imported first — verify via `applied.json` and a startup check that
  `demobank.__file__` is inside the copy; env loaded from `test-website/backend/.env` without printing it) and the
  patched frontend (`npm run dev -- --host localhost --port 5273 --strictPort` with
  `VITE_API_BASE_URL=http://localhost:8100`); wait for `/health` and the frontend; record PIDs in
  `runs/eval/<variant>/pids.json`. `stop <variant>` terminates them (process group). Ports configurable.
- `reset-db` — `make -C test-website/database reset`.
- `run <variant> [--jev-models m1,m2]` — reset-db, prepare, serve, then `qa-agent probe --url http://localhost:5273
  --out runs/eval/<variant>/run`, `verify`, `verdict --catalogue eval/catalogue.json`, `report`; always stop the
  servers (finally). With `--jev-models`, after the first verdict copy `checks.json`, `observations.json` and
  `evidence/` into `run-<model>` and rerun `verdict` with `TYPESAFE_MODEL=<model>` while the servers are still up
  (confirmation re-runs need them).
- `score [--variants ...]` — read each `runs/eval/<variant>/run*/verdicts.json`, write `runs/eval/metrics.json` and
  `runs/eval/metrics.md`.

Shared database: only one variant runs at a time; `run` resets the DB first. The baseline app on 8000/5173 may keep
running; variants use 8100/5273.

## Scoring (`score.py`)

Per variant run, with `F` = requirements the agent marked failed, `P` = passed, `U` = unverified:
- `expected = must_fail` (inherited from `base`); `allowed = expected ∪ may_fail`.
- TP = |F ∩ expected|; FP = |F − allowed| (false fails); FN_pass = |P ∩ expected| (false passes — worst);
  FN_unverified = |U ∩ expected| (missed but honest).
- precision = TP / (TP + FP) (None when no fails); recall = TP / |expected| (None when empty).
  On the fixed variant `now_correct` requirements reported failed are FP.
- Also report: counts P/F/U, coverage counts, Jev review probabilities for each failed requirement, cost total
  (from `cost.json`), probe runtime, and per-requirement rows for every expected or failed requirement.
- Overall table across variants plus micro-averaged precision and recall; mutant detection rate = mutants with ≥1
  of their own (non-inherited) must_fail requirements reported failed / mutants.
- Model comparison: when `run-<model>` dirs exist, the same metrics per model and the requirements whose status
  differs between models.

## Tests (`tests/test_eval.py`)

Patch application (exact count enforced; mismatch raises; base chaining order), scoring with hand-built verdict
dicts (TP/FP/FN_pass/FN_unverified, may_fail ignored, None-safe ratios, micro-averages, own-vs-inherited detection,
model diff), and that `qa_agent` never imports `eval` (scan the package source).

## Constraints

No changes to `test-website/`, `qa_agent/` behaviour or `agent/.dev/`. No commits. Never print `.env` contents. The
harness must not encode defects into the agent; it only builds variants and scores outputs. Keep the code compact and
in the style of the repo. Write `docs/P9_IMPLEMENTATION.md` (files, decisions where the spec was silent, test
results, and the exact commands to run each variant).
