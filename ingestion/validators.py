"""
Data integrity checks for normalized bhavcopy data.

Implements the M0 integrity rules: duplicate (symbol, trading_date) pairs,
OHLC sanity, and non-negative volume/traded_value. Each check is applied
independently and counted separately, so the ingestion report can say
*why* rows were rejected, not just how many.

Not yet implemented (deferred, tracked as open items for M0.2+):
  - trading-calendar cross-check for missing sessions
  - corporate-action-aware adjustment (raw prices vs research-adjusted
    prices are intentionally kept separate — this module only validates
    raw prices)
"""

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd


@dataclass
class ValidationResult:
    rows_received: int
    rows_accepted: int
    rows_rejected: int
    duplicates: int
    ohlc_violations: int
    missing_values: int
    negative_values: int
    errors: List[str] = field(default_factory=list)
    clean_df: Optional[pd.DataFrame] = None


def validate(df: pd.DataFrame) -> ValidationResult:
    rows_received = len(df)
    errors: List[str] = []
    working = df.copy()

    # 1. Duplicates on (symbol, trading_date) — keep first occurrence.
    dup_mask = working.duplicated(subset=["symbol", "trading_date"], keep="first")
    duplicates = int(dup_mask.sum())
    if duplicates:
        errors.append(f"{duplicates} duplicate (symbol, trading_date) rows dropped")
    working = working[~dup_mask].copy()

    # 2. Missing critical values (coercion in normalize() turns bad/blank
    #    numerics into NaN, so this also catches malformed source data).
    critical_cols = ["symbol", "open", "high", "low", "close", "volume"]
    missing_mask = working[critical_cols].isna().any(axis=1)
    missing_values = int(missing_mask.sum())
    if missing_values:
        errors.append(f"{missing_values} rows dropped for missing critical fields")
    working = working[~missing_mask].copy()

    # 3. OHLC sanity: High >= max(Open, Close, Low), Low <= min(Open, Close, High).
    high_ok = working["high"] >= working[["open", "close", "low"]].max(axis=1)
    low_ok = working["low"] <= working[["open", "close", "high"]].min(axis=1)
    ohlc_ok = high_ok & low_ok
    ohlc_violations = int((~ohlc_ok).sum())
    if ohlc_violations:
        errors.append(f"{ohlc_violations} rows failed OHLC sanity check")
    working = working[ohlc_ok].copy()

    # 4. Non-negative volume / traded value.
    non_negative = (working["volume"] >= 0) & (working["traded_value"] >= 0)
    negative_values = int((~non_negative).sum())
    if negative_values:
        errors.append(f"{negative_values} rows had negative volume/traded_value")
    working = working[non_negative].copy()

    rows_accepted = len(working)
    rows_rejected = rows_received - rows_accepted

    return ValidationResult(
        rows_received=rows_received,
        rows_accepted=rows_accepted,
        rows_rejected=rows_rejected,
        duplicates=duplicates,
        ohlc_violations=ohlc_violations,
        missing_values=missing_values,
        negative_values=negative_values,
        errors=errors,
        clean_df=working,
    )
