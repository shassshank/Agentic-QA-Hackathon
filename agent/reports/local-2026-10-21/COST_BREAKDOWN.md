# Cost breakdown: DemoBank QA audit

Estimated API and token cost of one full audit run. Source: the run's own meter, which records every
model call with its tokens, cache status and price
([`cost.json`](cost.json); per-phase totals in [`cost_breakdown.csv`](cost_breakdown.csv)).

Run `867ec2`, 2026-10-21, against a local copy of the target: 54 requirements, 41 browser scenarios
(174 s of browser time), 47 passed · 4 failed · 3 unverified.

## Summary

| | Cost (USD) | What it means |
|---|---:|---|
| **Cold run** | **≈ $0.17** | Auditing an application for the first time, with nothing cached. $0.169 measured, plus at most $0.006 for two cached answers whose token usage was not recorded (see notes). |
| **This run (billed)** | **$0.037** | What this run actually paid. The PRD catalogue and earlier decisions were reused from cache. |
| Browser scenarios, oracles, confirmation re-runs | $0 | No model is involved. |

About 73% of a cold run ($0.123) is the one-time reading of the PRD. Every decision after that costs
about 4.6 cents per run (cold).

## By phase

| Phase | What it does | Provider and model | Live calls | Cached | Input tokens | Output tokens | Billed | Cold |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Interpret | Turns the PRD into requirements | Gemini `gemini-3.8-flash` | 0 | 1 | 2,677 | 14,125 | $0 | $0.0550 |
| Interpret, repair | One correction pass after validation | Gemini `gemini-3.8-flash` | 0 | 1 | 15,715 | 14,956 | $0 | $0.0679 |
| Link | Which checks test which requirement | TypeSafe `jev-1.13.0` | 123 | 248 | 574,826 | 65,886 | $0.0166 | $0.0241 |
| Link escalation | Resolves uncertain links | Gemini `gemini-3.5-flash` | 1 | 0 | 12,688 | 2,550 | $0.0191 | $0.0191 |
| Judge | Wording checks ("clear", "direction shown") | TypeSafe `jev-1.13.0` | 56 | 63 | 60,098 | 2,609 | $0.0012 | $0.0025 |
| Judge escalation | Resolves uncertain wording | Gemini `gemini-3.5-flash-lite` | 0 | 2 | n/a | n/a | $0 | ≤ $0.006 |
| Review | Confirms each failure violates its requirement | TypeSafe `jev-1.13.0` | 4 | 1 | 8,456 | 272 | $0.0003 | $0.0004 |
| **Total** | | | **184** | **316** | **674,460** | **100,398** | **$0.037** | **≈ $0.17** |

Output tokens include Gemini thinking tokens (2,598), which are billed at the output rate.

## By provider

| Provider | Role | Cold | Billed |
|---|---|---:|---:|
| Google Gemini | PRD interpretation (once per PRD) and uncertain decisions | $0.142 (+ ≤ $0.006) | $0.019 |
| TypeSafe Jev | Linking, wording judgements, failure review (495 decisions) | $0.027 | $0.018 |

## Prices used

USD per million tokens, paid tier, checked 2026-10-21. They are defined in `agent/qa_agent/llm/meter.py`.

| Model | Input | Output | Notes |
|---|---:|---:|---|
| `gemini-3.8-flash` | 0.75 | 3.75 | List price until 2026-12-31; doubles to 1.50 / 7.50 from 2027-01-01 |
| `gemini-3.5-flash` | 0.75 | 3.75 | Not on Google's price page; priced like `gemini-3.8-flash` (estimate). Used here as the fallback while `gemini-3.8-flash` was rate-limited. |
| `gemini-3.5-flash-lite` | 0.30 | 2.50 | List price |
| `jev-1.13.0` (TypeSafe) | 0.042 | 0 | Output tokens are free |

## Projections

| Scenario | Estimate |
|---|---:|
| First audit of a new application (cold) | ≈ $0.17 |
| Repeat audit of the same application and PRD | $0.02–0.05 (this run: $0.037) |
| 100 first-time audits | ≈ $17 |
| 100 repeat audits | ≈ $2–5 |
| Cold run at 2027 Gemini prices | ≈ $0.31 |

A repeat audit pays only for decisions about observations that changed. Interpretation and unchanged
decisions come from cache.

## Why it is cheap

1. **The PRD is read once.** One model call, plus one repair, produces the requirement catalogue. It is
   cached by PRD, model and prompt.
2. **No model drives the browser.** All 41 scenarios, every check, and the confirmation re-runs are
   deterministic code.
3. **Small decisions use a small model.** Linking, wording judgements and failure review are typed
   yes/no or choice questions to TypeSafe Jev, at $0.042 per million input tokens with free output. That
   is 495 decisions for under three cents.
4. **Gemini only for uncertainty.** Only decisions Jev cannot make confidently go to Gemini: 3 calls
   in this run.
5. **Everything is cached by its exact inputs**, so nothing is paid twice for the same question.

## Notes

- **Cold versus billed.** Cold prices every call as if nothing were cached, including answers this
  run reused. Billed counts only the calls this run made.
- **Two answers without recorded usage.** Two Gemini judge answers were served from a cache written
  before token usage was stored, so their cost is not measured. Comparable calls in other runs used 181–1,762
  input and 104–1,030 output tokens on `gemini-3.5-flash-lite`, which is at most about $0.003 each. The cold total is
  therefore reported as ≈ $0.17 (measured $0.169, plus at most $0.006).
- **Provider-side cached input** is priced at the full input rate, an upper bound.
- **Not included:** browser compute and hosting, which are local and free here.
