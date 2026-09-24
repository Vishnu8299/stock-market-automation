"""
Test C — Final DB validation for M0.1 closure.

Three tests, as specified by Team Lead:
  C1 — Insert: real 2026-09-18 dataset into PostgreSQL
  C2 — Round-trip: SELECT records back and compare values
  C3 — Idempotency: re-run ingestion, verify no duplicates

These tests require a local PostgreSQL instance with TimescaleDB.
Configuration via .env or environment variables (PGHOST, PGPORT, etc.).

If PostgreSQL is unavailable, all tests are skipped gracefully.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.db import (
    get_engine,
    upsert_securities,
    insert_daily_prices,
    count_distinct_records,
    log_ingestion_run,
    InsertStats,
)
from ingestion.nse_source import NSEDataSource
from ingestion.validators import validate
from sqlalchemy import text


TRADING_DATE = date(2026, 9, 18)


# ======================================================================
# C1 — INSERT
# ======================================================================

class TestC1Insert:
    """C1: Insert the validated 2026-09-18 dataset into PostgreSQL."""

    def test_insert_all_rows(self, clean_db, sample_data_2026_09_18):
        """
        NSE file → parse → validate → PostgreSQL/TimescaleDB

        Records:
          source rows
          inserted rows
          updated rows (skipped due to ON CONFLICT DO NOTHING)
          rejected rows (missing security_id)
          database errors
        """
        engine = clean_db
        raw_file, result = sample_data_2026_09_18

        # Precondition: table is empty
        with engine.connect() as conn:
            before = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar()
        assert before == 0, "daily_prices should be empty before insert"

        # Step 1: Upsert securities
        upsert_securities(engine, result.clean_df)

        # Step 2: Insert daily prices
        stats = insert_daily_prices(engine, result.clean_df)

        # Step 3: Verify counts
        assert isinstance(stats, InsertStats)
        assert stats.source_rows == result.rows_accepted, (
            f"source_rows ({stats.source_rows}) should match accepted ({result.rows_accepted})"
        )
        assert stats.inserted > 0, "At least some rows should have been inserted"
        assert stats.db_errors == 0, f"No database errors expected, got {stats.db_errors}"
        assert stats.errors == 0, f"No missing-security errors expected, got {stats.errors}"
        # All accepted rows should be inserted on first run
        assert stats.inserted == result.rows_accepted, (
            f"First run: all {result.rows_accepted} accepted rows should be inserted, "
            f"got {stats.inserted} inserted + {stats.skipped} skipped"
        )
        assert stats.skipped == 0, "No rows should be skipped on first insert"

        # Step 4: Verify in database
        with engine.connect() as conn:
            after = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar()
        assert after == stats.inserted, (
            f"DB row count ({after}) should match inserted count ({stats.inserted})"
        )

        # Print C1 report
        print(f"\n{'='*50}")
        print(f"  C1 — INSERT REPORT")
        print(f"{'='*50}")
        print(f"  Source rows:     {stats.source_rows}")
        print(f"  Inserted rows:   {stats.inserted}")
        print(f"  Skipped rows:    {stats.skipped}")
        print(f"  Error rows:      {stats.errors}")
        print(f"  DB errors:       {stats.db_errors}")
        print(f"  DB total:        {after}")
        print(f"{'='*50}")

    def test_ingestion_run_logged(self, clean_db, sample_data_2026_09_18):
        """The ingestion run must be recorded in data_ingestion_runs."""
        engine = clean_db
        raw_file, result = sample_data_2026_09_18

        upsert_securities(engine, result.clean_df)
        insert_daily_prices(engine, result.clean_df)
        log_ingestion_run(
            engine,
            source="NSE_CM_UDIFF",
            trading_date=TRADING_DATE,
            file_hash=raw_file.sha256,
            row_count=result.rows_accepted,
            status="PASS",
        )

        with engine.connect() as conn:
            run = conn.execute(
                text(
                    "SELECT source, trading_date, row_count, validation_status "
                    "FROM data_ingestion_runs WHERE trading_date = :td"
                ),
                {"td": TRADING_DATE},
            ).fetchone()

        assert run is not None, "Ingestion run should be logged"
        assert run.source == "NSE_CM_UDIFF"
        assert run.trading_date == TRADING_DATE
        assert run.row_count == result.rows_accepted
        assert run.validation_status == "PASS"


# ======================================================================
# C2 — ROUND TRIP
# ======================================================================

class TestC2RoundTrip:
    """C2: SELECT representative records back and compare source == database."""

    def test_round_trip_ohlc_precision(self, clean_db, sample_data_2026_09_18):
        """
        Compare source values against database values for:
          - symbol
          - series
          - trading_date
          - open, high, low, close (NUMERIC precision)
          - volume
          - traded_value

        Tests at least 10 symbols across different series.
        """
        engine = clean_db
        raw_file, result = sample_data_2026_09_18
        clean_df = result.clean_df

        # Insert data first
        upsert_securities(engine, clean_df)
        insert_daily_prices(engine, clean_df)

        # Pick a diverse sample: first try to get symbols from multiple series
        series_list = clean_df["series"].unique()
        sample_symbols = []
        for series in series_list:
            series_df = clean_df[clean_df["series"] == series]
            sample_symbols.extend(
                series_df.head(max(1, 10 // len(series_list)))
                [["symbol", "series"]].values.tolist()
            )
        # Ensure at least 10
        if len(sample_symbols) < 10:
            remaining = clean_df[["symbol", "series"]].drop_duplicates().head(10)
            for _, row in remaining.iterrows():
                pair = [row["symbol"], row["series"]]
                if pair not in sample_symbols:
                    sample_symbols.append(pair)
                if len(sample_symbols) >= 10:
                    break

        assert len(sample_symbols) >= 10, (
            f"Need at least 10 symbols for round-trip, got {len(sample_symbols)}"
        )

        mismatches = []
        with engine.connect() as conn:
            for sym, series in sample_symbols:
                db_row = conn.execute(
                    text("""
                        SELECT s.symbol, s.series, dp.trading_date,
                               dp.open, dp.high, dp.low, dp.close,
                               dp.volume, dp.traded_value
                        FROM daily_prices dp
                        JOIN securities s ON s.id = dp.security_id
                        WHERE s.symbol = :symbol
                          AND s.series = :series
                          AND dp.trading_date = :td
                    """),
                    {"symbol": sym, "series": series, "td": TRADING_DATE},
                ).fetchone()

                assert db_row is not None, f"{sym}/{series} not found in database"

                # Get source row
                mask = (clean_df["symbol"] == sym) & (clean_df["series"] == series)
                source_row = clean_df[mask].iloc[0]

                # Compare each field — using Decimal for numeric precision
                checks = {
                    "symbol": db_row.symbol == sym,
                    "series": db_row.series == series,
                    "trading_date": db_row.trading_date == TRADING_DATE,
                    "open": abs(Decimal(str(db_row.open)) - Decimal(str(float(source_row["open"])))) < Decimal("0.01"),
                    "high": abs(Decimal(str(db_row.high)) - Decimal(str(float(source_row["high"])))) < Decimal("0.01"),
                    "low": abs(Decimal(str(db_row.low)) - Decimal(str(float(source_row["low"])))) < Decimal("0.01"),
                    "close": abs(Decimal(str(db_row.close)) - Decimal(str(float(source_row["close"])))) < Decimal("0.01"),
                    "volume": int(db_row.volume) == int(source_row["volume"]),
                    "traded_value": abs(
                        Decimal(str(db_row.traded_value)) -
                        Decimal(str(float(source_row["traded_value"])))
                    ) < Decimal("0.01"),
                }

                if not all(checks.values()):
                    failed = {k: v for k, v in checks.items() if not v}
                    mismatches.append({
                        "symbol": sym,
                        "series": series,
                        "failures": failed,
                        "db_open": str(db_row.open),
                        "source_open": str(source_row["open"]),
                        "db_close": str(db_row.close),
                        "source_close": str(source_row["close"]),
                        "db_volume": str(db_row.volume),
                        "source_volume": str(source_row["volume"]),
                    })

        # Print round-trip report
        print(f"\n{'='*50}")
        print(f"  C2 — ROUND-TRIP REPORT")
        print(f"{'='*50}")
        print(f"  Symbols tested:  {len(sample_symbols)}")
        print(f"  Mismatches:      {len(mismatches)}")
        for m in mismatches:
            print(f"    {m['symbol']}/{m['series']}: {m['failures']}")
        print(f"{'='*50}")

        assert len(mismatches) == 0, (
            f"Round-trip mismatches found: {mismatches}"
        )

    def test_round_trip_all_rows(self, clean_db, sample_data_2026_09_18):
        """Verify every single inserted row can be read back."""
        engine = clean_db
        raw_file, result = sample_data_2026_09_18
        clean_df = result.clean_df

        upsert_securities(engine, clean_df)
        stats = insert_daily_prices(engine, clean_df)

        with engine.connect() as conn:
            db_count = conn.execute(
                text("SELECT COUNT(*) FROM daily_prices WHERE trading_date = :td"),
                {"td": TRADING_DATE},
            ).scalar()

        assert db_count == stats.inserted, (
            f"All {stats.inserted} inserted rows should be readable, got {db_count}"
        )


# ======================================================================
# C3 — IDEMPOTENCY
# ======================================================================

class TestC3Idempotency:
    """C3: Run the same ingestion twice. No duplicates allowed."""

    def test_double_insert_no_duplicates(self, clean_db, sample_data_2026_09_18):
        """
        Run #1 → records inserted
        Run #2 → no duplicate records
        """
        engine = clean_db
        raw_file, result = sample_data_2026_09_18
        clean_df = result.clean_df

        # Run #1
        upsert_securities(engine, clean_df)
        stats_1 = insert_daily_prices(engine, clean_df)
        assert stats_1.inserted > 0, "Run #1 should insert records"

        # Run #2 — same data, same date
        upsert_securities(engine, clean_df)
        stats_2 = insert_daily_prices(engine, clean_df)

        assert stats_2.inserted == 0, (
            f"Run #2 should insert 0 new rows (idempotent), got {stats_2.inserted}"
        )
        assert stats_2.skipped == stats_1.inserted, (
            f"Run #2 should skip all {stats_1.inserted} rows, skipped {stats_2.skipped}"
        )

        print(f"\n{'='*50}")
        print(f"  C3 — IDEMPOTENCY REPORT")
        print(f"{'='*50}")
        print(f"  Run #1 inserted: {stats_1.inserted}")
        print(f"  Run #2 inserted: {stats_2.inserted}")
        print(f"  Run #2 skipped:  {stats_2.skipped}")
        print(f"{'='*50}")

    def test_distinct_count_invariant(self, clean_db, sample_data_2026_09_18):
        """
        COUNT(DISTINCT (security_id, trading_date)) == total relevant records

        The exact SQL depends on schema, but the invariant is what matters.
        """
        engine = clean_db
        raw_file, result = sample_data_2026_09_18
        clean_df = result.clean_df

        # Insert once
        upsert_securities(engine, clean_df)
        stats_1 = insert_daily_prices(engine, clean_df)

        # Insert again (idempotent)
        upsert_securities(engine, clean_df)
        insert_daily_prices(engine, clean_df)

        # Verify the invariant
        distinct_count = count_distinct_records(engine, TRADING_DATE)

        assert distinct_count == stats_1.inserted, (
            f"DISTINCT count ({distinct_count}) must equal first-run inserted ({stats_1.inserted})"
        )

        # Also verify via direct SQL to be explicit
        with engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM daily_prices WHERE trading_date = :td"),
                {"td": TRADING_DATE},
            ).scalar()

        assert total == distinct_count, (
            f"Total rows ({total}) must equal distinct rows ({distinct_count}) — "
            f"duplicates detected!"
        )

        print(f"\n{'='*50}")
        print(f"  C3 — DISTINCT COUNT INVARIANT")
        print(f"{'='*50}")
        print(f"  Total rows:      {total}")
        print(f"  Distinct records: {distinct_count}")
        print(f"  Match:           {'YES' if total == distinct_count else 'NO'}")
        print(f"{'='*50}")
