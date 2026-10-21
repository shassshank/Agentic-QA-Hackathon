# DemoBank QA audit

Target `http://localhost:5173` · run `867ec2` · generated 2026-10-21T21:41:45+00:00

**47 passed · 4 failed · 3 unverified** of 54 requirements (41/41 scenarios completed).

## Failed

### TRF-006 — Rejection of invalid amount inputs

> Blank, zero, negative, non-numeric, and over-precision values must be rejected with a clear validation message. Rejected input must not create a transaction or change balances.

- Expected: no 2xx POST — observed: `[{"url": "http://localhost:8000/api/transfers", "status": 201, "request": {"source_account_id": "00000000-0000-0000-0000-000000000101", "destination_account_id"`
- Expected: all balances unchanged and no new rows — observed: `{"deltas": {"•••• 1042": "0.00", "•••• 7781": "0.00"}, "new_rows": ["3690a7c6-4b05-4302-a68f-dc7bc8b18040"]}`
- Expected: no success feedback — observed: `{"alerts": [], "statuses": ["Transfer complete Amount $0.00 From Everyday Checking (•••• 1042) To Rainy Day Savings (•••• 7781) Date & time Oct 21, 2026, 11:43 `
- Expected: visible rejection explanation — observed: `["Transfer complete Amount $0.00 From Everyday Checking (•••• 1042) To Rainy Day Savings (•••• 7781) Date & time Oct 21, 2026, 11:43 AM PDT Memo qa-867ec2-inv-z`

Steps to reproduce:

1. Open /
2. Click the "Enter demo" button
3. Enter the demo (lands on /dashboard)
4. Open /dashboard
5. Open /transactions
6. Open /transfer
7. Choose "Everyday Checking (•••• 1042) — $4,369.75" in the From field
8. Choose "Rainy Day Savings (•••• 7781) — $8,275.00" in the To field
9. Type "0" into the Amount field
10. Type "qa-867ec2-inv-zero" into the Memo field
11. Click the "Transfer" button
12. Open /dashboard
13. Open /transactions

### TRF-008 — Rejection of transfer exceeding available balance

> The amount must not exceed the source account's available balance. If funds are insufficient, the transfer must be rejected, both balances must remain unchanged, and no completed transfer must appear in history.

- Expected: no 2xx POST — observed: `[{"url": "http://localhost:8000/api/transfers", "status": 201, "request": {"source_account_id": "00000000-0000-0000-0000-000000000101", "destination_account_id"`
- Expected: all balances unchanged and no new rows — observed: `{"deltas": {"•••• 1042": "-4369.76", "•••• 7781": "4369.76"}, "new_rows": ["25d99c3e-53a0-48e7-af7d-b4de8657095d"]}`
- Expected: no success feedback — observed: `{"alerts": [], "statuses": ["Transfer complete Amount $4,369.76 From Everyday Checking (•••• 1042) To Rainy Day Savings (•••• 7781) Date & time Oct 21, 2026, 11`
- Expected: visible rejection explanation — observed: `["Transfer complete Amount $4,369.76 From Everyday Checking (•••• 1042) To Rainy Day Savings (•••• 7781) Date & time Oct 21, 2026, 11:43 AM PDT Memo qa-867ec2-n`

Steps to reproduce:

1. Open /
2. Click the "Enter demo" button
3. Enter the demo (lands on /dashboard)
4. Open /dashboard
5. Open /transactions
6. Open /transfer
7. Choose "Everyday Checking (•••• 1042) — $4,369.75" in the From field
8. Choose "Rainy Day Savings (•••• 7781) — $8,275.00" in the To field
9. Type "4369.76" into the Amount field
10. Type "qa-867ec2-nsf" into the Memo field
11. Click the "Transfer" button
12. Open /dashboard
13. Open /transactions
14. Open /dashboard
15. Open /transfer
16. Choose "Rainy Day Savings (•••• 7781) — $12,644.76" in the From field
17. Choose "Everyday Checking (•••• 1042) — −$0.01" in the To field
18. Type "4369.76" into the Amount field
19. Type "qa-867ec2-restore" into the Memo field
20. Click the "Transfer" button
21. Open /dashboard

### HIS-007 — Filter history by date range

> The user can filter history by account, transaction type, status, and date range. When filters are applied, every displayed row must satisfy all selected filters.

- Expected: filtered set equals baseline predicate; end_only open..2025-04-10 — observed: `{"expected": ["00000000-0000-0000-0000-000000000201", "00000000-0000-0000-0000-000000000202", "00000000-0000-0000-0000-000000000203", "00000000-0000-0000-0000-0`

Steps to reproduce:

1. Open /
2. Click the "Enter demo" button
3. Enter the demo (lands on /dashboard)
4. Open /transactions
5. Type "2025-01-03" into the Start field
6. Type "2025-01-03" into the End field
7. Click the "Apply" button
8. Type "2025-04-10" into the Start field
9. Type "2025-04-10" into the End field
10. Click the "Apply" button
11. Type "2025-01-18" into the Start field
12. Type "2025-09-03" into the End field
13. Click the "Apply" button
14. Type "2025-04-10" into the Start field
15. Leave the End field as ""
16. Click the "Apply" button
17. Leave the Start field as ""
18. Type "2025-04-10" into the End field
19. Click the "Apply" button
20. Type "2025-09-03" into the Start field
21. Type "2025-01-18" into the End field
22. Click the "Apply" button

### HIS-009 — Inclusive date boundary filtering

> The start and end dates are inclusive. A transaction occurring on either boundary date must be included when it matches the other filters.

- Expected: filtered set equals baseline predicate; single_day_first 2025-01-03..2025-01-03 — observed: `{"expected": ["00000000-0000-0000-0000-000000000201"], "actual": [], "url": "/transactions?start_date=2025-01-03&end_date=2025-01-03"}`
- Expected: filtered set equals baseline predicate; single_day_mid 2025-04-10..2025-04-10 — observed: `{"expected": ["00000000-0000-0000-0000-000000000207"], "actual": [], "url": "/transactions?start_date=2025-04-10&end_date=2025-04-10"}`
- Expected: filtered set equals baseline predicate; boundaries_inner 2025-01-18..2025-09-03 — observed: `{"expected": ["00000000-0000-0000-0000-000000000202", "00000000-0000-0000-0000-000000000203", "00000000-0000-0000-0000-000000000204", "00000000-0000-0000-0000-0`
- Expected: filtered set equals baseline predicate; end_only open..2025-04-10 — observed: `{"expected": ["00000000-0000-0000-0000-000000000201", "00000000-0000-0000-0000-000000000202", "00000000-0000-0000-0000-000000000203", "00000000-0000-0000-0000-0`

Steps to reproduce:

1. Open /
2. Click the "Enter demo" button
3. Enter the demo (lands on /dashboard)
4. Open /transactions
5. Type "2025-01-03" into the Start field
6. Type "2025-01-03" into the End field
7. Click the "Apply" button
8. Type "2025-04-10" into the Start field
9. Type "2025-04-10" into the End field
10. Click the "Apply" button
11. Type "2025-01-18" into the Start field
12. Type "2025-09-03" into the End field
13. Click the "Apply" button
14. Type "2025-04-10" into the Start field
15. Leave the End field as ""
16. Click the "Apply" button
17. Leave the Start field as ""
18. Type "2025-04-10" into the End field
19. Click the "Apply" button
20. Type "2025-09-03" into the Start field
21. Type "2025-01-18" into the End field
22. Click the "Apply" button

## All requirements

| ID | Title | Status | Why |
|---|---|---|---|
| SES-001 | Landing page Enter Demo action entry | passed |  |
| SES-002 | Enter Demo action label readability | passed |  |
| SES-003 | Header customer identification and logout control | passed |  |
| SES-004 | Logout returns user to landing page | passed |  |
| SES-005 | Protected views inaccessible after logout | passed |  |
| SES-006 | Session preserved on page refresh | passed |  |
| SES-007 | Logged-out user cannot regain access via refresh | passed |  |
| ACC-001 | Dashboard displays checking and savings accounts | passed |  |
| ACC-002 | Account card required attributes | unverified | The linked checks ran but could not reach a definite result. |
| ACC-003 | Account name readability | passed |  |
| ACC-004 | Balance formatting to two decimal places | passed |  |
| ACC-005 | Prohibition of full account identifiers across UI | passed |  |
| ACC-006 | Account selection opens account detail view | passed |  |
| ACC-007 | Account balances consistency with transaction history | passed |  |
| TRF-001 | Transfer between owned accounts capability | passed |  |
| TRF-002 | Transfer form required input fields | passed |  |
| TRF-003 | Rejection of same-account transfers | unverified | A check failed, but the review found it does not violate this requirement. |
| TRF-004 | Same-account transfer error message clarity | passed |  |
| TRF-005 | Acceptance of valid transfer amount | passed |  |
| TRF-006 | Rejection of invalid amount inputs | failed |  |
| TRF-007 | Invalid amount validation message clarity | passed |  |
| TRF-008 | Rejection of transfer exceeding available balance | failed |  |
| TRF-009 | Atomicity of transfer balance updates | passed |  |
| TRF-010 | Transfer record creation across account histories | passed |  |
| TRF-011 | Success reporting after commit and failure state exclusivity | passed |  |
| TRF-012 | Transfer persistence across page refresh | passed |  |
| HIS-001 | Combined and account-specific history views | passed |  |
| HIS-002 | Transaction row required attributes | passed |  |
| HIS-003 | Transaction amount direction clarity | passed |  |
| HIS-004 | Filter history by account | passed |  |
| HIS-005 | Filter history by transaction type | passed |  |
| HIS-006 | Filter history by status | passed |  |
| HIS-007 | Filter history by date range | failed |  |
| HIS-008 | Combined multi-dimensional filtering | passed |  |
| HIS-009 | Inclusive date boundary filtering | failed |  |
| HIS-010 | Clear empty state on empty filter results | passed |  |
| HIS-011 | Filter clearing restores unfiltered history | passed |  |
| PRO-001 | Profile view synthetic customer details | passed |  |
| PRO-002 | Prohibition of sensitive and foreign user data exposure | passed |  |
| GEN-001 | Viewport responsiveness and usability | passed |  |
| GEN-002 | Interactive control labels readability | passed |  |
| GEN-003 | State communication for interactive controls | passed |  |
| GEN-004 | Prevent silent submission drops and duplicate submission | passed |  |
| GEN-005 | Navigation links and buttons destination accuracy | passed |  |
| GEN-006 | Recoverability from errors | passed |  |
| GEN-007 | No real payment or external bank network connections | passed |  |
| GEN-008 | Agent reporting of observed evidence against contract | unverified | The PRD statement cannot be checked from the browser. |
| SEED-001 | Seed data single customer with checking and savings accounts | passed |  |
| SEED-002 | Seed data account filter variety | passed |  |
| SEED-003 | Seed data transaction type variety | passed |  |
| SEED-004 | Seed data status variety | passed |  |
| SEED-005 | Seed data multiple dates variety | passed |  |
| SEED-006 | Seed data includes pre-existing transfer | passed |  |
| SEED-007 | Absence of unsupported banking features in seed application | passed |  |

## Cost

| Phase | Provider | Calls | Cached | Input tokens | Output tokens | This run (USD) | Cold run (USD) |
|---|---|---|---|---|---|---|---|
| interpret | gemini | 0 | 1 | 2677 | 14125 | 0.000000 | 0.054976 |
| interpret-repair | gemini | 0 | 1 | 15715 | 14956 | 0.000000 | 0.067871 |
| link | typesafe | 123 | 248 | 574826 | 65886 | 0.016608 | 0.024143 |
| link_escalation | gemini | 1 | 0 | 12688 | 2550 | 0.019079 | 0.019079 |
| judge | typesafe | 56 | 63 | 60098 | 2609 | 0.001200 | 0.002524 |
| judge_escalation | gemini | 0 | 2 | 0 | 0 | 0.000000 | 0.000000 |
| review | typesafe | 4 | 1 | 8456 | 272 | 0.000267 | 0.000355 |

This run: $0.037153 · cold run (no cache): $0.168948 (Some cached answers predate usage recording, so the cold estimate is a lower bound.)
