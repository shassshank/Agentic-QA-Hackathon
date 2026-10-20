from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .schemas import Account, AccountDelta, AccountSummary, Customer, Transaction, money, utc_timestamp


class Repository(Protocol):
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> None: ...
    async def create_session(self, token_hash: str, expires_at: datetime) -> Customer: ...
    async def customer_for_session(self, token_hash: str) -> Customer | None: ...
    async def revoke_session(self, token_hash: str) -> None: ...
    async def list_accounts(self, customer_id: UUID) -> list[Account]: ...
    async def get_account(self, customer_id: UUID, account_id: UUID) -> Account | None: ...
    async def list_transactions(
        self,
        customer_id: UUID,
        account_id: UUID | None,
        transaction_type: str | None,
        status: str | None,
        start_at: datetime | None,
        end_before: datetime | None,
    ) -> list[Transaction]: ...
    async def transfer(
        self,
        customer_id: UUID,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        memo: str | None,
    ) -> tuple[Transaction, list[Account]] | None: ...


def _customer(row: dict[str, Any]) -> Customer:
    return Customer(id=row["id"], display_name=row["display_name"], masked_email=row["masked_email"])


def _account(row: dict[str, Any]) -> Account:
    return Account(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        masked_number=row["masked_number"],
        currency=row["currency"].strip(),
        balance=money(row["balance"]),
    )


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self.pool = AsyncConnectionPool(
            database_url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")

    async def create_session(self, token_hash: str, expires_at: datetime) -> Customer:
        async with self.pool.connection() as conn, conn.transaction():
            customer_row = await (
                await conn.execute(
                    """
                    SELECT id, display_name, masked_email
                    FROM customers
                    ORDER BY created_at, id
                    LIMIT 1
                    """
                )
            ).fetchone()
            if customer_row is None:
                raise RuntimeError("The seeded demo customer is missing")
            await conn.execute(
                """
                INSERT INTO demo_sessions (id, customer_id, token_hash, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (uuid4(), customer_row["id"], token_hash, datetime.now(timezone.utc), expires_at),
            )
            return _customer(customer_row)

    async def customer_for_session(self, token_hash: str) -> Customer | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT c.id, c.display_name, c.masked_email
                    FROM demo_sessions s
                    JOIN customers c ON c.id = s.customer_id
                    WHERE s.token_hash = %s AND s.expires_at > CURRENT_TIMESTAMP
                    """,
                    (token_hash,),
                )
            ).fetchone()
            return _customer(row) if row else None

    async def revoke_session(self, token_hash: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM demo_sessions WHERE token_hash = %s", (token_hash,))

    async def list_accounts(self, customer_id: UUID) -> list[Account]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, name, type, masked_number, currency, balance
                    FROM accounts
                    WHERE customer_id = %s
                    ORDER BY created_at, id
                    """,
                    (customer_id,),
                )
            ).fetchall()
            return [_account(row) for row in rows]

    async def get_account(self, customer_id: UUID, account_id: UUID) -> Account | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, name, type, masked_number, currency, balance
                    FROM accounts
                    WHERE customer_id = %s AND id = %s
                    """,
                    (customer_id, account_id),
                )
            ).fetchone()
            return _account(row) if row else None

    async def list_transactions(
        self,
        customer_id: UUID,
        account_id: UUID | None,
        transaction_type: str | None,
        status: str | None,
        start_at: datetime | None,
        end_before: datetime | None,
    ) -> list[Transaction]:
        clauses = ["t.customer_id = %s"]
        params: list[Any] = [customer_id]
        if account_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM transaction_entries ae "
                "WHERE ae.transaction_id = t.id AND ae.account_id = %s)"
            )
            params.append(account_id)
        if transaction_type is not None:
            clauses.append("t.type = %s")
            params.append(transaction_type)
        if status is not None:
            clauses.append("t.status = %s")
            params.append(status)
        if start_at is not None:
            clauses.append("t.occurred_at >= %s")
            params.append(start_at)
        if end_before is not None:
            clauses.append("t.occurred_at < %s")
            params.append(end_before)

        query = f"""
            SELECT t.id, t.type, t.status, t.amount, t.memo, t.occurred_at
            FROM transactions t
            WHERE {' AND '.join(clauses)}
            ORDER BY t.occurred_at DESC, t.id
        """
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(query, params)).fetchall()
            return await self._hydrate_transactions(conn, rows)

    async def transfer(
        self,
        customer_id: UUID,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        memo: str | None,
    ) -> tuple[Transaction, list[Account]] | None:
        now = datetime.now(timezone.utc)
        transaction_id = uuid4()
        async with self.pool.connection() as conn, conn.transaction():
            # Stable lock order prevents concurrent opposite-direction transfers from deadlocking.
            locked = await (
                await conn.execute(
                    """
                    SELECT id
                    FROM accounts
                    WHERE customer_id = %s AND id = ANY(%s)
                    ORDER BY id
                    FOR UPDATE
                    """,
                    (customer_id, [source_account_id, destination_account_id]),
                )
            ).fetchall()
            if len(locked) != 2:
                return None

            await conn.execute(
                "UPDATE accounts SET balance = balance - %s WHERE id = %s",
                (amount, source_account_id),
            )
            await conn.execute(
                "UPDATE accounts SET balance = balance + %s WHERE id = %s",
                (amount, destination_account_id),
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (id, customer_id, type, status, amount, memo, occurred_at, created_at)
                VALUES (%s, %s, 'transfer', 'completed', %s, %s, %s, %s)
                """,
                (transaction_id, customer_id, amount, memo or None, now, now),
            )
            await conn.execute(
                """
                INSERT INTO transaction_entries
                    (id, transaction_id, account_id, amount_delta, created_at)
                VALUES
                    (%s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s)
                """,
                (
                    uuid4(), transaction_id, source_account_id, -amount, now,
                    uuid4(), transaction_id, destination_account_id, amount, now + timedelta(microseconds=1),
                ),
            )
            tx_row = await (
                await conn.execute(
                    """
                    SELECT id, type, status, amount, memo, occurred_at
                    FROM transactions WHERE id = %s
                    """,
                    (transaction_id,),
                )
            ).fetchone()
            transactions = await self._hydrate_transactions(conn, [tx_row])
            account_rows = await (
                await conn.execute(
                    """
                    SELECT id, name, type, masked_number, currency, balance
                    FROM accounts
                    WHERE id = ANY(%s)
                    ORDER BY CASE WHEN id = %s THEN 0 ELSE 1 END
                    """,
                    ([source_account_id, destination_account_id], source_account_id),
                )
            ).fetchall()
            return transactions[0], [_account(row) for row in account_rows]

    async def _hydrate_transactions(
        self, conn: AsyncConnection[dict[str, Any]], rows: Sequence[dict[str, Any]]
    ) -> list[Transaction]:
        if not rows:
            return []
        transaction_ids = [row["id"] for row in rows]
        entry_rows = await (
            await conn.execute(
                """
                SELECT e.transaction_id, e.account_id, e.amount_delta, e.created_at, e.id AS entry_id,
                       a.name, a.masked_number
                FROM transaction_entries e
                JOIN accounts a ON a.id = e.account_id
                WHERE e.transaction_id = ANY(%s)
                ORDER BY e.transaction_id, e.created_at, e.id
                """,
                (transaction_ids,),
            )
        ).fetchall()
        by_transaction: dict[UUID, list[dict[str, Any]]] = {}
        for entry in entry_rows:
            by_transaction.setdefault(entry["transaction_id"], []).append(entry)

        result: list[Transaction] = []
        for row in rows:
            entries = by_transaction.get(row["id"], [])
            source = None
            destination = None
            if row["type"] == "transfer" and entries:
                source_entry = next(
                    (entry for entry in entries if entry["amount_delta"] == -row["amount"]),
                    entries[0],
                )
                destination_entry = next(
                    (entry for entry in entries if entry is not source_entry),
                    entries[-1],
                )
                source = self._summary(source_entry)
                destination = self._summary(destination_entry)
            elif row["type"] == "withdrawal" and entries:
                source = self._summary(entries[0])
            elif row["type"] == "deposit" and entries:
                destination = self._summary(entries[0])
            result.append(
                Transaction(
                    id=row["id"],
                    type=row["type"],
                    status=row["status"],
                    amount=money(row["amount"]),
                    memo=row["memo"],
                    occurred_at=utc_timestamp(row["occurred_at"]),
                    source_account=source,
                    destination_account=destination,
                    account_deltas=[
                        AccountDelta(account_id=entry["account_id"], amount_delta=money(entry["amount_delta"]))
                        for entry in entries
                    ],
                )
            )
        return result

    @staticmethod
    def _summary(entry: dict[str, Any]) -> AccountSummary:
        return AccountSummary(
            id=entry["account_id"], name=entry["name"], masked_number=entry["masked_number"]
        )
