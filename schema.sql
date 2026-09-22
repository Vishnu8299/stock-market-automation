-- M0 schema: securities, daily_prices (TimescaleDB hypertable),
-- corporate_actions, data_ingestion_runs.
-- Run once against a PostgreSQL database with the TimescaleDB extension
-- available: psql -d market_research -f schema.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS securities (
    id            SERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL,
    exchange      TEXT NOT NULL DEFAULT 'NSE',
    isin          TEXT,
    company_name  TEXT,
    series        TEXT,
    active_from   DATE,
    active_to     DATE,
    UNIQUE (symbol, exchange)
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

-- Partition by trading_date so range queries (backtesting over a date
-- window) stay fast as the table grows.
SELECT create_hypertable('daily_prices', 'trading_date', if_not_exists => TRUE);

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
