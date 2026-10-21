# Walkthrough video script (target 4:00, hard limit 5:00)

Recording setup: terminal on the left, browser on the right, 1080p. Use `--headed` for the live browser
segment. Pre-warm nothing except the PRD catalogue cache, so the decision costs shown are real.
Replace `<URL>` with the hosted target.

| Time | On screen | Narration (approximate) |
|---|---|---|
| 0:00–0:20 | Title card, then `test-website/PRD.md` | "The question: does DemoBank behave the way its specification says? The agent gets two inputs — this PRD and a URL — and nothing else. It never reads the app's source." |
| 0:20–0:55 | `qa-agent interpret --prd ../test-website/PRD.md`, then `runs/requirements.json` scrolled | "One model call turns the PRD into atomic requirements — 54 in the reference run. Each must quote the PRD verbatim; code validates the quotes and coverage of every bullet, with one repair call if anything is off. The result is cached, so this costs about eleven cents once per PRD." |
| 0:55–1:50 | `qa-agent probe --url <URL> --only trf.reject.zero --only his.filter.dates --only ses.refresh_after_logout --headed` — browser driving | "41 scenarios: normal, negative and edge. Here it tries a zero-amount transfer, filters history on boundary dates, and checks that the back button doesn't restore a logged-out session. No model is involved in driving — locators are role and label based, with self-healing if a control is renamed. Every step is recorded: screenshots, API calls, balances before and after." |
| 1:50–2:30 | `qa-agent verify` output; open `checks.json` on the zero-transfer check | "Verification is deterministic. A success message isn't a pass — the oracle compares balances and new transaction rows. Here the app accepted a $0.00 transfer: the API returned 201 and a new row appeared. That's a check failure, with expected and observed values." |
| 2:30–3:10 | `qa-agent verdict` output (link → judge → confirm → review) | "Then the decisions. TypeSafe Jev links checks to requirements and judges wording, returning probabilities rather than prose. A failure only counts if the scenario fails again on a re-run and a review says it actually violates that requirement — otherwise it's reported as unverified, never guessed." |
| 3:10–3:50 | `report.html` in the browser: summary → a failed requirement (steps, expected/observed, screenshot) → "How checks were linked" → Cost | "The report: confirmed defects — zero and negative amounts accepted, overdrafts allowed, and the date filter dropping the end date — each with plain-language reproduction steps and evidence. Cost: what this run billed and the cold-run price side by side." |
| 3:50–4:10 | `docs/P9_IMPLEMENTATION.md` results table | "We also measured it: against seven injected defects and a fixed build, precision 0.97 and zero false failures on the fixed build. The misses are honest unverifieds, not wrong passes." |
| 4:10–4:20 | README architecture diagram | "Code decides, models interpret, uncertainty stays unverified. Repo link in the description." |

Checklist before recording: target reachable (`qa-agent doctor`), DB in seed state, `.env` not visible on
screen, terminal history cleared of keys, browser profile without personal tabs. Check the `--only`
probe ids against `qa-agent probe --list` and the defects named at 3:10 against the hosted run's report.
