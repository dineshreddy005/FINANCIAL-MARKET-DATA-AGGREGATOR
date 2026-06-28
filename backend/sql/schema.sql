-- ============================================================================
-- Financial Market Data Aggregator -- Core Schema
-- Designed for idempotent ingestion: re-processing the exact same payload
-- (e.g. due to a network retry or a re-delivered webhook) must NEVER create
-- a duplicate row. We achieve this two ways:
--   1. A natural-key UNIQUE constraint (source, symbol, event_time) so the
--      same tick/bar from the same source always lands on the same row.
--   2. A content hash (payload_hash) so we can cheaply detect "this exact
--      payload was already applied" and skip the write entirely (DO NOTHING)
--      vs. "this is a correction for the same key with new values"
--      (DO UPDATE).
-- ============================================================================

CREATE TABLE IF NOT EXISTS assets (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(32)  NOT NULL,
    asset_type      VARCHAR(16)  NOT NULL CHECK (asset_type IN ('equity', 'crypto', 'fx', 'index')),
    display_name    VARCHAR(128),
    currency        VARCHAR(8)   NOT NULL DEFAULT 'USD',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (symbol, asset_type)
);

-- The single canonical "price fact" table that every source -- yfinance
-- streaming quotes, CoinGecko REST polling, EOD batch CSV/JSON -- normalizes
-- into. This is the "idempotent normalization pipeline" surface.
CREATE TABLE IF NOT EXISTS price_ticks (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    source          VARCHAR(32)  NOT NULL,           -- 'yfinance' | 'coingecko' | 'eod_csv' | 'eod_json'
    event_time      TIMESTAMPTZ  NOT NULL,            -- the timestamp THE DATA carries, not ingestion time
    price           NUMERIC(20, 8) NOT NULL,
    volume          NUMERIC(24, 4),
    open            NUMERIC(20, 8),
    high            NUMERIC(20, 8),
    low             NUMERIC(20, 8),
    close           NUMERIC(20, 8),
    payload_hash    CHAR(64) NOT NULL,                -- sha256 of the raw normalized payload
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- Natural key: one row per (asset, source, point-in-time)
    UNIQUE (asset_id, source, event_time)
);

CREATE INDEX IF NOT EXISTS idx_price_ticks_asset_time ON price_ticks (asset_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_price_ticks_source ON price_ticks (source);

-- Tracks ingestion batches (one row per file upload or websocket session)
-- purely for observability/audit -- "did this batch get applied, and how
-- many rows were inserted vs. deduped?"
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(32) NOT NULL,
    mode            VARCHAR(16) NOT NULL CHECK (mode IN ('stream', 'batch')),
    file_name       VARCHAR(256),
    rows_received   INTEGER NOT NULL DEFAULT 0,
    rows_inserted   INTEGER NOT NULL DEFAULT 0,
    rows_deduped    INTEGER NOT NULL DEFAULT 0,
    rows_updated    INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed'))
);

-- Circuit breaker audit trail (Redis holds the live state; this is the
-- durable history of OPEN/CLOSED transitions for dashboards/alerts).
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id              BIGSERIAL PRIMARY KEY,
    provider        VARCHAR(32) NOT NULL,
    from_state      VARCHAR(16) NOT NULL,
    to_state        VARCHAR(16) NOT NULL,
    reason          TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Non-volatile market metadata -- the "cold" columns on an asset (sector,
-- market cap, descriptive blurb, 52-week range). These change at most once a
-- day, which is exactly the profile that benefits from a Redis cache-aside
-- layer in front of Postgres (see requirement 6 / app/cache.py).
-- ============================================================================
ALTER TABLE assets ADD COLUMN IF NOT EXISTS sector          VARCHAR(64);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS market_cap      NUMERIC(24, 2);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS description     TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS week52_high     NUMERIC(20, 8);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS week52_low      NUMERIC(20, 8);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- ============================================================================
-- Requirement 4: Zero-Trust Access Control & Field Masking
-- ----------------------------------------------------------------------------
-- `users` backs RBAC -- every JWT's `role` claim is minted from this table at
-- login, never trusted from the client. `client_accounts` / `account_holdings`
-- model brokerage-style accounts that carry genuinely sensitive identifiers
-- (account_number, routing_number) -- exactly the fields app/masking.py
-- redacts to "**** 9104" style for any caller whose JWT role is not 'admin',
-- regardless of whose data it is (zero trust: the API never decides what to
-- reveal based on who *owns* a row, only on the verified role of the
-- *caller*).
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(64) UNIQUE NOT NULL,
    email           VARCHAR(128) UNIQUE NOT NULL,
    full_name       VARCHAR(128) NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'client', 'service')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS client_accounts (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_number  VARCHAR(32) NOT NULL,            -- masked to last 4 for non-admin roles
    routing_number  VARCHAR(32),                      -- masked to last 4 for non-admin roles
    broker_name     VARCHAR(64) NOT NULL DEFAULT 'FMDA Prime Brokerage',
    account_type    VARCHAR(16) NOT NULL DEFAULT 'individual'
                        CHECK (account_type IN ('individual', 'ira', 'corporate')),
    cash_balance    NUMERIC(18, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_holdings (
    id              SERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES client_accounts(id) ON DELETE CASCADE,
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    quantity        NUMERIC(20, 8) NOT NULL DEFAULT 0,
    avg_cost        NUMERIC(20, 8) NOT NULL DEFAULT 0,
    UNIQUE (account_id, asset_id)
);

-- ============================================================================
-- Requirement 5: Comprehensive Audit Trail & Temporal Logging
-- ----------------------------------------------------------------------------
-- A single generic trigger function, attached to every table whose writes
-- must be immutably logged. It captures the FULL old/new row as JSONB (not
-- just the changed columns) so compliance can answer "what did this record
-- look like at time T" for any T. The acting identity is read from a
-- transaction-local Postgres setting (`app.current_actor`) that application
-- code sets at the top of every write transaction via
-- `SELECT set_config('app.current_actor', :actor, true)` -- `true` makes it
-- LOCAL to the transaction, so it can never leak across pooled connections.
-- This means the audit trail is enforced AT THE DATABASE LAYER: even a stray
-- manual `UPDATE` run directly in psql still gets logged (falls back to
-- 'system' as the actor when the app didn't set one).
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    table_name      VARCHAR(64) NOT NULL,
    record_id       VARCHAR(64) NOT NULL,
    operation       VARCHAR(10) NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    changed_by      VARCHAR(128) NOT NULL,
    old_data        JSONB,
    new_data        JSONB,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_table_record ON audit_logs (table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_changed_at ON audit_logs (changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_changed_by ON audit_logs (changed_by);

CREATE OR REPLACE FUNCTION fn_audit_log() RETURNS TRIGGER AS $$
DECLARE
    v_actor TEXT;
BEGIN
    v_actor := COALESCE(current_setting('app.current_actor', true), 'system');
    INSERT INTO audit_logs (table_name, record_id, operation, changed_by, old_data, new_data, changed_at)
    VALUES (
        TG_TABLE_NAME,
        COALESCE((NEW.id)::text, (OLD.id)::text),
        TG_OP,
        v_actor,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE row_to_json(OLD)::jsonb END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE row_to_json(NEW)::jsonb END,
        now()
    );
    -- AFTER triggers ignore the return value for the underlying DML, but a
    -- well-formed trigger function still returns a row of the correct type.
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Automated data overwrites from an API feed (the idempotent UPSERT's
-- "DO UPDATE" path in app/normalizer.py) -- routine first-time INSERTs are
-- ordinary ingestion and are deliberately NOT logged here so the audit
-- table stays focused on *overwrites*, exactly as required; every
-- correction/restatement of an existing tick is still caught.
DROP TRIGGER IF EXISTS trg_audit_price_ticks ON price_ticks;
CREATE TRIGGER trg_audit_price_ticks
    AFTER UPDATE ON price_ticks
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log();

-- Manual adjustments by an administrator (balance corrections, account
-- detail edits) -- both the creation and any later edit are logged.
DROP TRIGGER IF EXISTS trg_audit_client_accounts ON client_accounts;
CREATE TRIGGER trg_audit_client_accounts
    AFTER INSERT OR UPDATE ON client_accounts
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log();

DROP TRIGGER IF EXISTS trg_audit_account_holdings ON account_holdings;
CREATE TRIGGER trg_audit_account_holdings
    AFTER INSERT OR UPDATE ON account_holdings
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log();
