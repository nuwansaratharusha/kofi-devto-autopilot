-- ============================================================================
-- Double-Entry Idempotent Ledger & Webhook Deduplication Schema
-- Compatible with SQLite 3.37+ (STRICT/WAL) and PostgreSQL 14+
-- Integer minor units (cents) only — NEVER store currency in IEEE-754 floats.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ledger_accounts (
    account_id TEXT PRIMARY KEY,
    account_name TEXT NOT NULL UNIQUE,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    normal_side TEXT NOT NULL CHECK (normal_side IN ('DEBIT', 'CREDIT')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_transactions (
    tx_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    external_source TEXT NOT NULL,      -- e.g. 'kofi', 'polar', 'stripe', 'lemonsqueezy'
    external_event_id TEXT NOT NULL,
    description TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    UNIQUE (external_source, external_event_id)
);

CREATE TABLE IF NOT EXISTS ledger_postings (
    posting_id TEXT PRIMARY KEY,
    tx_id TEXT NOT NULL REFERENCES ledger_transactions(tx_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES ledger_accounts(account_id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_postings_tx ON ledger_postings(tx_id);
CREATE INDEX IF NOT EXISTS idx_ledger_postings_account ON ledger_postings(account_id);

-- View computing signed balances per account without mutable balance columns
CREATE VIEW IF NOT EXISTS v_account_balances AS
SELECT
    a.account_id,
    a.account_name,
    a.currency,
    a.normal_side,
    COALESCE(
        SUM(
            CASE
                WHEN p.direction = a.normal_side THEN p.amount_cents
                WHEN p.direction IS NOT NULL THEN -p.amount_cents
                ELSE 0
            END
        ),
        0
    ) AS balance_cents
FROM ledger_accounts a
LEFT JOIN ledger_postings p ON p.account_id = a.account_id
GROUP BY a.account_id, a.account_name, a.currency, a.normal_side;
