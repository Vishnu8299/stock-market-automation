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
-- Adjusted series belong in adjusted_prices (M0 rule: raw != adjusted).
--
-- M0.2.3 HYBRID ARCHITECTURE (Team Lead approved 2026-09-25):
--   corporate_actions  — versioned, auditable event log
--   adjusted_prices    — pre-computed indicator-safe series
--   daily_prices       — immutable raw prices (never modified)

-- Corporate action events — the versioned, auditable event log.
-- Each event type uses the relevant subset of columns.
CREATE TABLE IF NOT EXISTS corporate_actions (
    id                   SERIAL PRIMARY KEY,
    security_id          INTEGER NOT NULL REFERENCES securities(id),
    action_type          TEXT NOT NULL,
        -- Supported: 'SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS',
        --            'MERGER', 'DEMERGER', 'SYMBOL_CHANGE'
    ex_date              DATE NOT NULL,
    record_date          DATE,

    -- Split / Bonus: ratio_from:ratio_to (e.g. 1:5 split = from=1, to=5)
    ratio_from           INTEGER,
    ratio_to             INTEGER,

    -- Dividend: amount per share in INR
    dividend_amount      NUMERIC(12,4),
    dividend_type        TEXT,  -- 'INTERIM', 'FINAL', 'SPECIAL'

    -- Rights: issue price per share
    rights_price         NUMERIC(12,2),
    rights_ratio_from    INTEGER,  -- e.g. 1 right for every 5 held
    rights_ratio_to      INTEGER,

    -- Merger / Demerger: related security and swap ratio
    related_security_id  INTEGER REFERENCES securities(id),
    swap_ratio_from      INTEGER,
    swap_ratio_to        INTEGER,

    -- Symbol change: new symbol name (old is the parent security)
    new_symbol           TEXT,

    -- Provenance
    source               TEXT NOT NULL DEFAULT 'MANUAL',
    raw_data             JSONB,          -- original source record verbatim
    event_version        TEXT NOT NULL DEFAULT '1.0',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (security_id, action_type, ex_date)
);

-- Pre-computed adjusted prices for indicator/signal calculation.
-- Derived from daily_prices + corporate_actions. Never used for
-- portfolio accounting (that uses event-driven simulation on raw prices).
--
-- Adjustment scope is configurable per computation:
--   'SPLIT_BONUS'       — splits + bonuses only (default for SMA/indicators)
--   'SPLIT_BONUS_DIV'   — splits + bonuses + dividends (for total return)
CREATE TABLE IF NOT EXISTS adjusted_prices (
    security_id       INTEGER NOT NULL REFERENCES securities(id),
    trading_date      DATE NOT NULL,
    adj_open          NUMERIC(12,4),
    adj_high          NUMERIC(12,4),
    adj_low           NUMERIC(12,4),
    adj_close         NUMERIC(12,4),
    adj_volume        BIGINT,
    adjustment_factor NUMERIC(16,8) NOT NULL,  -- cumulative multiplier
    adjustment_scope  TEXT NOT NULL DEFAULT 'SPLIT_BONUS',
    event_version     TEXT NOT NULL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (security_id, trading_date, adjustment_scope)
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
CREATE INDEX IF NOT EXISTS idx_corporate_actions_security ON corporate_actions(security_id, ex_date);
CREATE INDEX IF NOT EXISTS idx_adjusted_prices_security ON adjusted_prices(security_id, trading_date);
