"""
Database layer for M0: PostgreSQL + TimescaleDB.

Run schema.sql once against your database before using this module —
it only handles connections and inserts, not migrations.

Inserts are idempotent: re-running ingest.py for a date that's already
loaded does not create duplicates (ON CONFLICT DO NOTHING on
(security_id, trading_date)), so ingest.py is safe to re-run.
"""

import os
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def get_engine() -> Engine:
    """
    Connection info comes from environment variables, never hardcoded:
        PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    """
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    dbname = os.environ.get("PGDATABASE", "market_research")
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
    return create_engine(url)


def upsert_securities(engine: Engine, df: pd.DataFrame) -> None:
    """Ensure every symbol appearing in this day's data exists in `securities`."""
    securities = df[["symbol", "isin", "series"]].drop_duplicates()
    with engine.begin() as conn:
        for row in securities.itertuples(index=False):
            conn.execute(
                text(
                    """
                    INSERT INTO securities (symbol, isin, series, exchange)
                    VALUES (:symbol, :isin, :series, 'NSE')
                    ON CONFLICT (symbol, exchange) DO UPDATE
                        SET isin = EXCLUDED.isin, series = EXCLUDED.series
                    """
                ),
                {"symbol": row.symbol, "isin": row.isin, "series": row.series},
            )


def insert_daily_prices(engine: Engine, df: pd.DataFrame) -> int:
    """Insert validated rows into daily_prices. Returns rows actually inserted."""
    inserted = 0
    with engine.begin() as conn:
        sec_ids = dict(
            conn.execute(
                text("SELECT symbol, id FROM securities WHERE exchange = 'NSE'")
            ).fetchall()
        )
        for row in df.itertuples(index=False):
            security_id = sec_ids.get(row.symbol)
            if security_id is None:
                continue
            result = conn.execute(
                text(
                    """
                    INSERT INTO daily_prices
                        (security_id, trading_date, open, high, low, close,
                         last_price, previous_close, volume, traded_value, source)
                    VALUES
                        (:security_id, :trading_date, :open, :high, :low, :close,
                         :last_price, :previous_close, :volume, :traded_value, :source)
                    ON CONFLICT (security_id, trading_date) DO NOTHING
                    """
                ),
                {
                    "security_id": security_id,
                    "trading_date": row.trading_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "last_price": row.last_price,
                    "previous_close": row.previous_close,
                    "volume": row.volume,
                    "traded_value": row.traded_value,
                    "source": row.source,
                },
            )
            inserted += result.rowcount
    return inserted


def log_ingestion_run(
    engine: Engine,
    source: str,
    trading_date: date,
    file_hash: str,
    row_count: int,
    status: str,
    error_details: Optional[str] = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_ingestion_runs
                    (source, trading_date, downloaded_at, file_hash,
                     row_count, validation_status, error_details)
                VALUES
                    (:source, :trading_date, now(), :file_hash,
                     :row_count, :status, :error_details)
                """
            ),
            {
                "source": source,
                "trading_date": trading_date,
                "file_hash": file_hash,
                "row_count": row_count,
                "status": status,
                "error_details": error_details,
            },
        )
