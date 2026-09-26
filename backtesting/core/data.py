"""
Data interface — abstract source for OHLCV price data.

Two concrete implementations ship with M0.2:
  CsvDataSource     — reads from a CSV file on disk
  DataFrameSource   — wraps an in-memory DataFrame (useful in tests)

A future PostgresDataSource can query the `daily_prices` hypertable
from schema.sql; we don't build it now since no Postgres instance is
available in this environment (same constraint as M0.1).

The canonical output schema matches `daily_prices`:
    trading_date  DATE
    open          NUMERIC
    high          NUMERIC
    low           NUMERIC
    close         NUMERIC
    volume        BIGINT
"""

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


class DataInterface(ABC):
    """Abstract base for any OHLCV data source."""

    @abstractmethod
    def load(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """
        Return a DataFrame with columns:
            [trading_date, open, high, low, close, volume]
        sorted by trading_date ascending, filtered to [start, end] inclusive.

        Raises ValueError if no data is available for the given symbol/range.
        """


class CsvDataSource(DataInterface):
    """
    Read OHLCV data from a single CSV file.

    Expected CSV columns (case-insensitive matching):
        trading_date (or date), open, high, low, close, volume

    An optional 'symbol' column is used when the CSV holds multiple
    tickers.  If absent, every row is assumed to belong to the
    requested symbol.
    """

    REQUIRED_COLS = {"open", "high", "low", "close", "volume"}

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {self.csv_path}")

    def load(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        df = pd.read_csv(self.csv_path)
        df.columns = df.columns.str.strip().str.lower()

        # Normalize date column name
        if "trading_date" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "trading_date"})

        missing = self.REQUIRED_COLS - set(df.columns)
        if missing or "trading_date" not in df.columns:
            raise ValueError(
                f"CSV missing required columns: {missing | ({'trading_date'} - set(df.columns))}"
            )

        df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date

        # Filter by symbol if the column exists
        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == symbol.upper()].copy()

        if df.empty:
            raise ValueError(f"No data found for symbol '{symbol}' in {self.csv_path}")

        # Date range filter
        if start is not None:
            df = df[df["trading_date"] >= start]
        if end is not None:
            df = df[df["trading_date"] <= end]

        if df.empty:
            raise ValueError(
                f"No data for '{symbol}' in range {start}–{end}"
            )

        # Coerce numerics
        for col in self.REQUIRED_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("trading_date").reset_index(drop=True)
        return df[["trading_date", "open", "high", "low", "close", "volume"]]


class DataFrameSource(DataInterface):
    """
    Wrap an in-memory DataFrame as a DataInterface.

    Useful for unit tests where constructing a DataFrame inline
    is simpler than writing a CSV to disk.
    """

    def __init__(self, df: pd.DataFrame):
        required = {"trading_date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        self._df = df.copy()

    def load(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        df = self._df.copy()

        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == symbol.upper()].copy()

        if start is not None:
            df = df[df["trading_date"] >= start]
        if end is not None:
            df = df[df["trading_date"] <= end]

        if df.empty:
            raise ValueError(f"No data for '{symbol}' in the provided DataFrame")

        df = df.sort_values("trading_date").reset_index(drop=True)
        return df[["trading_date", "open", "high", "low", "close", "volume"]]
