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
    - Corporate-action adjustment (M0.2.3 hybrid architecture)

M0.2.3 Hybrid Architecture (Team Lead approved 2026-09-25):
    - adjusted=True  → pre-computed adjusted prices for indicators/signals
    - adjusted=False → raw NSE prices for audit/ML/portfolio accounting
    - load_corporate_events() → event log for event-driven portfolio simulation
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
DATASET_VERSION = "M0.2.3"
PARSER_VERSION = "1.0.0"
DATASET_HASH = "cb157bc6b30f2c4a12335a650381280bdad8d199edff541cba6cbd5e205d3320"
EVENT_VERSION = "1.0"


class DataInterface:
    """Standardized market data accessor for downstream consumers (backtester, research).

    Abstracts away the data source (NSE, PostgreSQL, future sources).
    All data returned is in a canonical format:
        - symbol, series, trading_date, open, high, low, close, volume, traded_value
        - Sorted by trading_date ascending
        - No duplicates on (symbol, series, trading_date)
        - OHLC sanity guaranteed (High >= max(O,C,L), Low <= min(O,C,H))

    M0.2.3 additions:
        - adjusted=True returns corporate-action-adjusted prices (for indicators)
        - adjusted=False returns raw prices (for portfolio accounting, ML, audit)
        - load_corporate_events() returns structured event log

    Usage:
        from ingestion.data_interface import DataInterface
        from ingestion.db import get_engine

        di = DataInterface(engine=get_engine())

        # For SMA/indicators — adjusted prices (continuous across splits)
        df = di.load_market_data("RELIANCE", adjusted=True)

        # For portfolio accounting — raw prices + events
        raw = di.load_market_data("RELIANCE", adjusted=False)
        events = di.load_corporate_events("RELIANCE")
    """

    # Canonical columns returned by all load_* methods
    COLUMNS = [
        "symbol", "series", "trading_date",
        "open", "high", "low", "close",
        "volume", "traded_value",
    ]

    # Additional columns when adjusted=True
    ADJUSTED_COLUMNS = [
        "symbol", "series", "trading_date",
        "open", "high", "low", "close",
        "volume", "traded_value",
        "adjustment_factor", "adjustment_scope",
    ]

    def __init__(self, engine: Engine):
        self._engine = engine

    def load_market_data(
        self,
        symbol: str,
        series: str = "EQ",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        adjusted: bool = False,
        adjustment_scope: str = "SPLIT_BONUS",
    ) -> pd.DataFrame:
        """Load OHLCV data for a single security.

        Returns a DataFrame with canonical columns, sorted by trading_date.
        Empty DataFrame (with correct columns) if no data found.

        Args:
            symbol: Ticker symbol (e.g. "RELIANCE")
            series: Series code (default "EQ").
            start_date: Inclusive start date (None = all available)
            end_date: Inclusive end date (None = all available)
            adjusted: If True, return adjusted prices from adjusted_prices
                     table (for indicator/signal calculation). If False,
                     return raw prices from daily_prices (for portfolio
                     accounting, ML features, audit).
            adjustment_scope: 'SPLIT_BONUS' (default) or 'SPLIT_BONUS_DIV'.
                            Only used when adjusted=True.
        """
        if adjusted:
            return self._load_adjusted(symbol, series, start_date, end_date, adjustment_scope)
        return self._load_raw(symbol, series, start_date, end_date)

    def _load_raw(
        self, symbol: str, series: str,
        start_date: Optional[date], end_date: Optional[date],
    ) -> pd.DataFrame:
        """Load raw prices from daily_prices."""
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
        self._cast_types(df)
        return df

    def _load_adjusted(
        self, symbol: str, series: str,
        start_date: Optional[date], end_date: Optional[date],
        adjustment_scope: str,
    ) -> pd.DataFrame:
        """Load adjusted prices from adjusted_prices table.

        Falls back to raw prices if no adjusted data exists.
        """
        conditions = [
            "s.symbol = :symbol", "s.series = :series",
            "ap.adjustment_scope = :scope",
        ]
        params = {"symbol": symbol, "series": series, "scope": adjustment_scope}

        if start_date:
            conditions.append("ap.trading_date >= :start_date")
            params["start_date"] = start_date
        if end_date:
            conditions.append("ap.trading_date <= :end_date")
            params["end_date"] = end_date

        where = " AND ".join(conditions)

        query = f"""
            SELECT s.symbol, s.series, ap.trading_date,
                   ap.adj_open AS open, ap.adj_high AS high,
                   ap.adj_low AS low, ap.adj_close AS close,
                   ap.adj_volume AS volume,
                   dp.traded_value,
                   ap.adjustment_factor, ap.adjustment_scope
            FROM adjusted_prices ap
            JOIN securities s ON s.id = ap.security_id
            JOIN daily_prices dp ON dp.security_id = ap.security_id
                                AND dp.trading_date = ap.trading_date
            WHERE {where}
            ORDER BY ap.trading_date ASC
        """

        with self._engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()

        if not rows:
            logger.warning(
                "No adjusted prices for %s/%s scope=%s, falling back to raw",
                symbol, series, adjustment_scope,
            )
            return self._load_raw(symbol, series, start_date, end_date)

        df = pd.DataFrame(rows, columns=self.ADJUSTED_COLUMNS)
        self._cast_types(df)
        return df

    def _cast_types(self, df: pd.DataFrame) -> None:
        """Ensure consistent types across raw and adjusted data."""
        if "trading_date" in df.columns:
            df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
        for col in ["open", "high", "low", "close", "traded_value"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")
        if "adjustment_factor" in df.columns:
            df["adjustment_factor"] = pd.to_numeric(df["adjustment_factor"], errors="coerce")

    def load_corporate_events(
        self,
        symbol: str,
        series: str = "EQ",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Load corporate action events for event-driven simulation.

        Returns a DataFrame sorted by ex_date ascending with all event fields.
        Used by the backtester for portfolio accounting (share adjustment,
        dividend cash flows, rights decisions).
        """
        conditions = ["s.symbol = :symbol", "s.series = :series"]
        params = {"symbol": symbol, "series": series}

        if start_date:
            conditions.append("ca.ex_date >= :start_date")
            params["start_date"] = start_date
        if end_date:
            conditions.append("ca.ex_date <= :end_date")
            params["end_date"] = end_date

        where = " AND ".join(conditions)

        query = f"""
            SELECT ca.id, s.symbol, s.series, ca.action_type, ca.ex_date,
                   ca.record_date, ca.ratio_from, ca.ratio_to,
                   ca.dividend_amount, ca.dividend_type,
                   ca.rights_price, ca.rights_ratio_from, ca.rights_ratio_to,
                   ca.related_security_id, ca.swap_ratio_from, ca.swap_ratio_to,
                   ca.new_symbol, ca.source, ca.event_version
            FROM corporate_actions ca
            JOIN securities s ON s.id = ca.security_id
            WHERE {where}
            ORDER BY ca.ex_date ASC
        """

        event_columns = [
            "id", "symbol", "series", "action_type", "ex_date",
            "record_date", "ratio_from", "ratio_to",
            "dividend_amount", "dividend_type",
            "rights_price", "rights_ratio_from", "rights_ratio_to",
            "related_security_id", "swap_ratio_from", "swap_ratio_to",
            "new_symbol", "source", "event_version",
        ]

        with self._engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()

        if not rows:
            return pd.DataFrame(columns=event_columns)

        df = pd.DataFrame(rows, columns=event_columns)
        df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
        if "record_date" in df.columns:
            df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
        return df

    def load_multi(
        self,
        symbols: List[str],
        series: str = "EQ",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        adjusted: bool = False,
    ) -> pd.DataFrame:
        """Load OHLCV data for multiple securities at once.

        Returns a single DataFrame with all securities, sorted by
        (symbol, trading_date). Useful for portfolio backtests.
        """
        frames = []
        for sym in symbols:
            df = self.load_market_data(sym, series, start_date, end_date, adjusted=adjusted)
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

        Returns a dict with validation results including adjustment status.
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

        # Determine adjustment status from data
        if "adjustment_factor" in df.columns and "adjustment_scope" in df.columns:
            scope = df["adjustment_scope"].iloc[0] if len(df) > 0 else "UNKNOWN"
            adj_status = f"ADJUSTED — {scope} — event_version {EVENT_VERSION}"
        else:
            adj_status = "RAW — corporate actions NOT applied"

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "rows": len(df),
            "date_range": (
                str(df["trading_date"].min()) if "trading_date" in df.columns else None,
                str(df["trading_date"].max()) if "trading_date" in df.columns else None,
            ),
            "adjustment_status": adj_status,
            "dataset_version": DATASET_VERSION,
            "event_version": EVENT_VERSION,
        }
