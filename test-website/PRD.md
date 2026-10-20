# DemoBank Product Requirements

## Purpose

DemoBank is a small fictional banking dashboard used as the target application for an agentic QA hackathon. A user should be able to enter a demo session, inspect their accounts and transaction history, transfer funds between their own accounts, and view basic profile information. All records are synthetic. The application must never initiate real payments or connect to an external bank.

This document is the behavioral contract for the target. Implementations may have defects; agents should compare observed behavior with these requirements and report evidence rather than assuming a workflow succeeded.

## Users and access

- The target has one seeded demo customer.
- The landing page provides a clearly labeled **Enter Demo** action. It opens the demo dashboard without requiring a password or real identity information.
- The header identifies the active demo customer and provides a **Log out** action.
- Logging out returns the user to the landing page. Protected dashboard views should not remain usable after logout; direct navigation to them should return the user to the landing page or an access-required state.
- Refreshing while in a valid demo session should preserve that session. A logged-out user must not regain access merely by refreshing a protected page.

## Dashboard and accounts

- The dashboard shows the customer's checking and savings accounts.
- Each account displays a readable account name, a masked account identifier, account type, and current available balance in USD.
- Balances are displayed to two decimal places. The UI must not expose a full account identifier.
- Selecting an account opens an account detail view with its current balance and related transaction history.
- Account balances shown on the dashboard and detail view must agree with the latest successful transactions.

## Transfers between owned accounts

- The user can transfer funds between the checking and savings accounts owned by the demo customer.
- The transfer form includes source account, destination account, amount, and an optional memo.
- The source and destination must be different accounts. Same-account transfers must be rejected with a clear explanation and must not alter balances or create a transaction.
- Amount must be a positive number greater than zero, with no more than two digits after the decimal point. Blank, zero, negative, non-numeric, and over-precision values must be rejected with a clear validation message. Rejected input must not create a transaction or change balances.
- The amount must not exceed the source account's available balance. If funds are insufficient, the transfer must be rejected, both balances must remain unchanged, and no completed transfer must appear in history.
- A valid transfer is atomic: the source balance decreases by the amount and the destination balance increases by the same amount. No partial transfer is allowed.
- A successful transfer creates one completed transfer record that can be found in both relevant account histories. The record shows the amount, date and time, source, destination, status, and memo if supplied.
- The UI reports success only after the transfer has been committed. If a transfer is rejected or fails, the UI must explain that it did not complete and must not show a success state.
- Refreshing after a successful transfer must preserve the updated balances and transaction record.

## Transaction history

- The user can view a combined transaction history and account-specific history.
- Each row shows date and time, description or memo, transaction type, amount, status, and relevant account. Amount direction should be clear for deposits, withdrawals, and transfers.
- The user can filter history by account, transaction type, status, and date range. When filters are applied, every displayed row must satisfy all selected filters.
- The start and end dates are inclusive. A transaction occurring on either boundary date must be included when it matches the other filters.
- An empty result set must show a clear empty state, not an error or unrelated transactions.
- Clearing filters restores the unfiltered history.

## Profile

- A profile view displays only synthetic demo-customer details, such as display name and a masked email address.
- The view must not reveal passwords, authentication tokens, full account identifiers, or data belonging to another user.

## General interface behavior

- The target works in a current desktop browser at a 1280 × 800 viewport and remains usable at narrower widths.
- Interactive controls have readable labels and communicate loading, success, validation, and failure states.
- A submitted action should not silently disappear. While waiting, the UI should show a loading state or otherwise prevent accidental duplicate submission.
- Navigation links and buttons lead to the labeled destination or perform the labeled action.
- A recoverable error must leave the user able to retry or return to the dashboard.

## Seed data expectations

The demo begins with one customer, one checking account, one savings account, and enough historical transactions to exercise account, type, status, and date filters. Seed records should include multiple dates and at least one transfer between the two accounts. The demo has no signup, external payment, bill-pay, loan, card, or multi-user features.

## Out of scope

- Real bank integrations, payment rails, or deposits from external sources.
- Signup, password reset, multi-factor authentication, or production-grade identity verification.
- Multiple customers, joint accounts, loans, credit cards, budgeting, or investment features.
- Mobile-native applications.

## Requirement coverage expectations

A QA report should map its checks to the behaviors above and label each requirement **passed**, **failed**, or **unverified**. A failure should include the requirement, steps to reproduce, expected behavior, observed behavior, and relevant evidence such as a screenshot or visible values. If the agent cannot safely or reliably verify a behavior, it should explain why and mark it unverified rather than claiming a pass.
