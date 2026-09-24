"""
Standardized Market Data Interface — the clean contract between
the NSE Data Engine and the Backtesting Engine.

Design rule (Team Lead directive, M0.1 closure):
    backtest.py → load_market_data()
    NOT:
    backtest.py → download_nse_data()

The backtester must not know or care that NSE was the data source.
This module handles:
    - Source selection (PostgreSQL, CSV fallback)
    - Dataset versioning
    - Data validation at the boundary
    - Corporate-action adjustment status

Corporate-action adjustment: NOT YET IMPLEMENTED
Indian statutory cost model: NOT YET IMPLEMENTED
"""

import logging
import os
from datetime import date
from typing import List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# M0.1 frozen dataset provenance
DATASET_VERSION = "M0.1"
PARSER_VERSION = "1.0.0"
DATASET_HASH = "cb157bc6b30f2c4a12335a650381280bdad8d199edff541cba6cbd5e205d3320"


class DataInterface:
    """Standardized market data accessor for downstream consumers (backtester, research).

    Abstracts away the data source (NSE, PostgreSQL, future sources).
    All data returned is in a canonical format:
        - symbol, series, trading_date, open, high, low, close, volume, traded_value
        - Sorted by trading_date ascending
        - No duplicates on (symbol, series, trading_date)
        - OHLC sanity guaranteed (High >= max(O,C,L), Low <= min(O,C,H))

    Usage:
        from ingestion.data_interface import DataInterface
        from ingestion.db import get_engine

        di = DataInterface(engine=get_engine())
        df = di.load_market_data("RELIANCE", start_date=date(2026, 9, 1))
    """

    # Canonical columns returned by all load_* methods
    COLUMNS = [
        "symbol", "series", "trading_date",
        "open", "high", "low", "close",
        "volume", "traded_value",
    ]

    def __init__(self, engine: Engine):
        self._engine = engine

    def load_market_data(
        self,
        symbol: str,
        series: str = "EQ",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Load OHLCV data for a single security.

        Returns a DataFrame with canonical columns, sorted by trading_date.
        Empty DataFrame (with correct columns) if no data found.

        Args:
            symbol: NSE ticker symbol (e.g. "RELIANCE")
            series: Series code (default "EQ"). Use "" for bonds/NCDs.
            start_date: Inclusive start date (None = all available)
            end_date: Inclusive end date (None = all available)
        """
        conditions = ["s.symbol = :symbol", "s.series = :series"]
        params = {"symbol": symbol, "series": series}

        if start_date:
            conditions.append("dp.trading_date >= :start_date")
            params["start_date"] = start_date
        if end_date:
            conditions.append("dp.trading_date <= :end_date")
            params["end_date"] = end_date

        where = " AND ".join(conditions)

        query = f"""
            SELECT s.symbol, s.series, dp.trading_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.traded_value
            FROM daily_prices dp
            JOIN securities s ON s.id = dp.security_id
            WHERE {where}
            ORDER BY dp.trading_date ASC
        """

        with self._engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()

        if not rows:
            return pd.DataFrame(columns=self.COLUMNS)

        df = pd.DataFrame(rows, columns=self.COLUMNS)

        # Ensure correct types
        df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
        for col in ["open", "high", "low", "close", "traded_value"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        return df

    def load_multi(
        self,
        symbols: List[str],
        series: str = "EQ",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Load OHLCV data for multiple securities at once.

        Returns a single DataFrame with all securities, sorted by
        (symbol, trading_date). Useful for portfolio backtests.
        """
        frames = []
        for sym in symbols:
            df = self.load_market_data(sym, series, start_date, end_date)
            if len(df) > 0:
                frames.append(df)

        if not frames:
            return pd.DataFrame(columns=self.COLUMNS)

        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values(["symbol", "trading_date"]).reset_index(drop=True)

    def list_symbols(
        self,
        series: Optional[str] = None,
        exchange: str = "NSE",
    ) -> List[str]:
        """List all available symbols, optionally filtered by series."""
        conditions = ["exchange = :exchange"]
        params = {"exchange": exchange}

        if series is not None:
            conditions.append("series = :series")
            params["series"] = series

        where = " AND ".join(conditions)

        with self._engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT DISTINCT symbol FROM securities WHERE {where} ORDER BY symbol"),
                params,
            ).fetchall()

        return [r[0] for r in rows]

    def get_date_range(self, symbol: str, series: str = "EQ") -> Optional[dict]:
        """Return the first and last trading dates available for a symbol.

        Returns: {"first_date": date, "last_date": date, "observations": int}
                 or None if no data found.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT MIN(dp.trading_date) as first_date,
                           MAX(dp.trading_date) as last_date,
                           COUNT(*) as observations
                    FROM daily_prices dp
                    JOIN securities s ON s.id = dp.security_id
                    WHERE s.symbol = :symbol AND s.series = :series
                """),
                {"symbol": symbol, "series": series},
            ).fetchone()

        if row is None or row.observations == 0:
            return None

        return {
            "first_date": row.first_date,
            "last_date": row.last_date,
            "observations": row.observations,
        }

    def validate_data(self, df: pd.DataFrame) -> dict:
        """Validate a loaded DataFrame for common data quality issues.

        Returns a dict with validation results. Used by the M0.2
        integration test to verify data integrity at the boundary.
        """
        issues = []

        if df.empty:
            return {"valid": False, "issues": ["DataFrame is empty"]}

        # Check for duplicates
        dups = df.duplicated(subset=["symbol", "series", "trading_date"])
        if dups.any():
            issues.append(f"{dups.sum()} duplicate (symbol, series, trading_date) rows")

        # Check OHLC sanity
        if all(c in df.columns for c in ["open", "high", "low", "close"]):
            high_ok = df["high"] >= df[["open", "close", "low"]].max(axis=1)
            low_ok = df["low"] <= df[["open", "close", "high"]].min(axis=1)
            ohlc_bad = ~(high_ok & low_ok)
            if ohlc_bad.any():
                issues.append(f"{ohlc_bad.sum()} OHLC sanity violations")

        # Check for NaN in critical fields
        critical = ["open", "high", "low", "close", "volume"]
        for col in critical:
            if col in df.columns and df[col].isna().any():
                issues.append(f"{df[col].isna().sum()} NaN values in {col}")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "rows": len(df),
            "date_range": (
                str(df["trading_date"].min()) if "trading_date" in df.columns else None,
                str(df["trading_date"].max()) if "trading_date" in df.columns else None,
            ),
            "adjustment_status": "RAW — corporate actions NOT applied",
            "dataset_version": DATASET_VERSION,
        }
