# Agentic QA Hackathon Target Application

This repository contains the sample web application participants will test and a separate workspace for their QA agents. The target is a fictional banking dashboard with synthetic data only; it does not connect to real banks, payment networks, or financial accounts.

Before you start the hackathon challenge, please make sure to fork the repository.

## Repository layout

```text
Agentic-AQ-Hackathon
├── README.md
├── test-website/
│   ├── PRD.md
│   ├── README.md
│   ├── backend/       # target API service source
│   ├── frontend/      # target browser app source
│   └── database/      # target database migrations and seed data
└── agent/
    └── README.md      # participant QA agent workspace
```

`test-website/` is the application under test. Its PRD is the behavioral contract: your agent should decide whether the running site satisfies it. `agent/` is where your team builds its QA system. The sample app will be hosted separately; the organizers will provide its URL.

## Challenge

Given the application URL and PRD, build an AI-powered QA agent that can:

1. Extract verifiable requirements from the PRD.
2. Generate and execute normal, negative, and edge-case scenarios.
3. Explore and operate the web application autonomously.
4. Compare observed behavior with expected behavior.
5. Report passed, failed, and unverified requirements with reproducible evidence.

The core question is: **Does the application behave the way its specification says it should?**

## What to submit

- A public GitHub or GitLab repository containing your agent source, setup instructions, and an architectural README.
- A 3–5 minute walkthrough showing the agent reading the specification, operating the site, and producing results.
- An exported QA audit report with requirement-level results, bug evidence and reproduction steps, and an estimated API/token cost breakdown.

Do not submit credentials, real personal data, or secrets. The target uses fake data. Do not attempt denial-of-service testing or access infrastructure outside the supplied application.

## Judging rubric

| Criterion | Weight | What judges look for |
|---|---:|---|
| Accuracy and defect detection | 30% | Finds real defects and missing requirements with few false positives. |
| Evaluation and verification rigor | 25% | Tests positive, negative, and edge-case behavior and supports its verdicts. |
| Cost of running the agent | 20% | Reports practical model/API cost and uses resources efficiently. |
| Autonomous resilience | 15% | Handles async UI changes and unexpected states without brittle scripts. |
| Evidence and actionability | 10% | Gives useful screenshots, observed values, and reproduction steps. |

## Target application notes

The application offers a visible **Enter Demo** action; no account or password is needed. All data is synthetic and isolated to the demo. Read [`test-website/PRD.md`](test-website/PRD.md) as the source of expected behavior. The site is intentionally imperfect, so a successful click is not proof that a requirement passed. Verify resulting balances, transaction records, filters, and error states.

## Agent workspace

Use the `agent/` directory for your project, or link your own repository there. Keep the target application and QA agent logically separate. See [`agent/README.md`](agent/README.md) for submission expectations.
