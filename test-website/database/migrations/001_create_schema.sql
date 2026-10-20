BEGIN;

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY,
    display_name TEXT NOT NULL,
    masked_email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_sessions (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('checking', 'savings')),
    masked_number TEXT NOT NULL UNIQUE,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    balance NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    type TEXT NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'transfer')),
    status TEXT NOT NULL CHECK (status IN ('completed', 'pending', 'failed')),
    amount NUMERIC(12,2) NOT NULL,
    memo TEXT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS transaction_entries (
    id UUID PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    amount_delta NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (transaction_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_transaction_entries_account_created_at
    ON transaction_entries (account_id, created_at);

CREATE INDEX IF NOT EXISTS idx_transactions_customer_occurred_at
    ON transactions (customer_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_transactions_customer_type_occurred_at
    ON transactions (customer_id, type, occurred_at);

CREATE INDEX IF NOT EXISTS idx_transactions_customer_status_occurred_at
    ON transactions (customer_id, status, occurred_at);

COMMIT;
