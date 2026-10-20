# DemoBank database

This directory owns the PostgreSQL schema and deterministic demo data for the shared
DemoBank application. It creates exactly the five tables in the frozen integration
contract. The backend is the only application layer that should connect to them.

## Start and reset

Docker Compose exposes PostgreSQL 16 on `localhost:5432`. It uses these local development
credentials:

```text
DATABASE_URL=postgresql://demobank:demobank_local@localhost:5432/demobank
```

From this directory:

```bash
make up       # starts PostgreSQL; a new volume runs migration 001, then the seed
make reset    # reapplies the idempotent migration and restores the seed
make down     # stops PostgreSQL and retains its data volume
```

`make destroy` removes the local database volume and all of its data. The next `make up`
performs a clean initialization.

## Migration order

Apply `migrations/001_create_schema.sql`, then `seed.sql`. The migration creates, in
foreign-key order, `customers`, `demo_sessions`, `accounts`, `transactions`, and
`transaction_entries`, followed by their indexes. `seed.sql` truncates those tables in
one transaction and restores the same fixed UUIDs, timestamps, and values on every run.

The seed contains one fictional customer, checking and savings accounts, and 12
transactions across several dates, types, and statuses. Pending and failed transactions
have zero-delta entries so they remain associated with an account without changing its
available balance.

## Schema notes

- UUIDs are supplied by the backend for runtime records and are fixed in seed data.
- All stored timestamps are `TIMESTAMPTZ`; seed literals include an explicit UTC offset.
- Money uses `NUMERIC(12,2)`. Transfer validation is handled by the backend service.
- All foreign keys use `ON DELETE RESTRICT`. Session token hashes are unique, and raw
  opaque session tokens must never be stored.
- A transaction entry associates history with an account. Completed transfers have one
  negative source entry and one positive destination entry, with a zero net delta.
- Seeded checking and savings opening balances are `2500.00` and `8000.00`. Stored
  balances equal each opening balance plus all corresponding seeded entry deltas.
