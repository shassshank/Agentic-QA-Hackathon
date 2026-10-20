"""DEV ONLY: probe the local DemoBank API to build ground truth for the eval set.

Never imported by the agent. Mutates local data; run `make reset` in test-website/database afterwards.
Usage: uv run python eval/probe_api.py [--api http://localhost:8000]
"""

from __future__ import annotations

import argparse

import httpx

CHECKING = "00000000-0000-0000-0000-000000000101"
SAVINGS = "00000000-0000-0000-0000-000000000102"


def balances(client: httpx.Client) -> dict[str, str]:
    return {a["name"]: a["balance"] for a in client.get("/api/accounts").json()["accounts"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    with httpx.Client(base_url=args.api, timeout=10) as client:
        print("session", client.post("/api/demo/session").status_code)
        print("start balances", balances(client))

        cases = [
            ("zero", CHECKING, SAVINGS, "0"),
            ("zero-2dp", CHECKING, SAVINGS, "0.00"),
            ("negative", CHECKING, SAVINGS, "-5"),
            ("non-numeric", CHECKING, SAVINGS, "abc"),
            ("over-precision", CHECKING, SAVINGS, "1.234"),
            ("same-account", CHECKING, CHECKING, "1"),
            ("overdraft", CHECKING, SAVINGS, "99999"),
        ]
        for name, src, dst, amount in cases:
            r = client.post(
                "/api/transfers",
                json={"source_account_id": src, "destination_account_id": dst, "amount": amount, "memo": f"probe {name}"},
            )
            body = r.json()
            detail = body.get("error", {}).get("code") or f"tx status {body['transaction']['status']}"
            print(f"{name:15} HTTP {r.status_code}  {detail}")
        print("end balances", balances(client))

        for start, end in (("2025-01-03", "2025-01-03"), ("2025-01-03", "2025-01-04")):
            rows = client.get("/api/transactions", params={"start_date": start, "end_date": end}).json()["transactions"]
            print(f"filter {start}..{end}: {len(rows)} rows (seed has 1 tx at 2025-01-03 09:00 PT)")

        print("logout", client.delete("/api/demo/session").status_code)
        print("me after logout", client.get("/api/me").status_code)


if __name__ == "__main__":
    main()
