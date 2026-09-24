-- M0 schema: securities, daily_prices (optional TimescaleDB hypertable),
-- corporate_actions, data_ingestion_runs.
-- Run once against a PostgreSQL database:
--   psql -d market_research -f schema.sql
--
-- TimescaleDB is optional: if the extension is available, daily_prices
-- becomes a hypertable for fast range queries. On plain PostgreSQL, the
-- same tables/constraints/indexes work correctly — hypertable is a
-- performance optimization, not a correctness requirement.
--
-- UPDATED during M0.1.1 live validation:
--   - securities UNIQUE changed from (symbol, exchange) to (symbol, series, exchange)
--     because NSE lists the same symbol in multiple series (e.g. ENTERO in BL and EQ).

-- Try to enable TimescaleDB; skip gracefully if not installed.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
    RAISE NOTICE 'TimescaleDB extension enabled.';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB not available — running on plain PostgreSQL.';
END
$$;

CREATE TABLE IF NOT EXISTS securities (
    id            SERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL,
    exchange      TEXT NOT NULL DEFAULT 'NSE',
    isin          TEXT,
    company_name  TEXT,
    series        TEXT NOT NULL DEFAULT 'EQ',
    active_from   DATE,
    active_to     DATE,
    UNIQUE (symbol, series, exchange)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    security_id     INTEGER NOT NULL REFERENCES securities(id),
    trading_date    DATE NOT NULL,
    open            NUMERIC(12,2),
    high            NUMERIC(12,2),
    low             NUMERIC(12,2),
    close           NUMERIC(12,2),
    last_price      NUMERIC(12,2),
    previous_close  NUMERIC(12,2),
    volume          BIGINT,
    traded_value    NUMERIC(18,2),
    source          TEXT NOT NULL,
    PRIMARY KEY (security_id, trading_date)
);

-- If TimescaleDB is available, partition by trading_date so range
-- queries (backtesting over a date window) stay fast as the table grows.
DO $$
BEGIN
    PERFORM create_hypertable('daily_prices', 'trading_date', if_not_exists => TRUE);
    RAISE NOTICE 'daily_prices converted to TimescaleDB hypertable.';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Skipping hypertable creation (TimescaleDB not available).';
END
$$;

-- raw prices are stored as reported; splits/bonuses are NOT applied here.
-- Adjusted series belong in a separate research table once corporate
-- actions are wired up (M0 rule: raw prices != research-adjusted prices).
CREATE TABLE IF NOT EXISTS corporate_actions (
    id              SERIAL PRIMARY KEY,
    security_id     INTEGER NOT NULL REFERENCES securities(id),
    action_type     TEXT NOT NULL,      -- e.g. 'SPLIT', 'BONUS', 'DIVIDEND'
    ex_date         DATE NOT NULL,
    record_date     DATE,
    adjustment_info JSONB
);

CREATE TABLE IF NOT EXISTS data_ingestion_runs (
    id                SERIAL PRIMARY KEY,
    source            TEXT NOT NULL,
    trading_date      DATE NOT NULL,
    downloaded_at     TIMESTAMPTZ NOT NULL,
    file_hash         TEXT NOT NULL,
    row_count         INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    error_details     TEXT
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_security ON daily_prices(security_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_date ON data_ingestion_runs(trading_date);
