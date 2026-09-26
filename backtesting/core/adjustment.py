"""
Corporate actions -- data model and price adjustment engine.

Implements the Hybrid approach (Approach C) from the M0.2.3 design:
  1. Price adjustment: produces a research-adjusted price series
     for indicator computation (SMA sees continuous prices).
  2. Portfolio adjustment: provides data for the execution simulator
     to adjust share count / avg_cost on ex-dates.

Key types:
  CorporateAction     -- a single corporate-action event
  AdjustmentFactor    -- multiplicative factors derived from an action
  PriceAdjuster       -- produces an adjusted DataFrame from raw + actions

Supported action types (V1):
  BONUS, SPLIT, SYMBOL_CHANGE

Unsupported (V1 -- will halt if encountered):
  RIGHTS_ISSUE, MERGER, DEMERGER, BUYBACK

Acknowledged but not price-adjusted (V1):
  DIVIDEND -- logged as warning, does not halt
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Supported and unsupported action types ──────────────────────────

SUPPORTED_ACTIONS = {"BONUS", "SPLIT", "SYMBOL_CHANGE"}
UNSUPPORTED_ACTIONS = {"RIGHTS_ISSUE", "MERGER", "DEMERGER", "BUYBACK"}
WARN_ONLY_ACTIONS = {"DIVIDEND"}


# ── Data Model ──────────────────────────────────────────────────────

@dataclass
class CorporateAction:
    """A single corporate-action event for a security."""

    security_symbol: str
    action_type: str        # 'BONUS', 'SPLIT', 'SYMBOL_CHANGE', 'DIVIDEND', etc.
    ex_date: date
    record_date: date | None = None

    # For BONUS: ratio_new=1, ratio_existing=1 means 1:1
    #   (1 new share for every 1 held)
    # For SPLIT: ratio_new=2, ratio_existing=1 means 2-for-1
    ratio_new: int | None = None
    ratio_existing: int | None = None

    # For DIVIDEND: amount per share
    dividend_per_share: float | None = None

    # For SYMBOL_CHANGE
    old_symbol: str | None = None
    new_symbol: str | None = None

    def __post_init__(self):
        self.action_type = self.action_type.upper()
        self._validate()

    def _validate(self):
        """Validate fields based on action type."""
        if self.action_type in ("BONUS", "SPLIT"):
            if self.ratio_new is None or self.ratio_existing is None:
                raise ValueError(
                    f"{self.action_type} requires ratio_new and ratio_existing"
                )
            if self.ratio_new <= 0 or self.ratio_existing <= 0:
                raise ValueError(
                    f"{self.action_type} ratios must be positive, got "
                    f"{self.ratio_new}:{self.ratio_existing}"
                )
        elif self.action_type == "SYMBOL_CHANGE":
            if self.old_symbol is None or self.new_symbol is None:
                raise ValueError(
                    "SYMBOL_CHANGE requires old_symbol and new_symbol"
                )
        elif self.action_type == "DIVIDEND":
            if self.dividend_per_share is None:
                raise ValueError("DIVIDEND requires dividend_per_share")
        elif self.action_type in UNSUPPORTED_ACTIONS:
            pass  # Validation deferred to runtime (halt on encounter)
        else:
            raise ValueError(f"Unknown action_type: '{self.action_type}'")


@dataclass
class AdjustmentFactor:
    """
    Multiplicative factors derived from a corporate action.

    price_factor:  multiply all prices BEFORE ex_date by this
    volume_factor: multiply all volumes BEFORE ex_date by this
    share_factor:  multiply portfolio share count by this ON ex_date
    """

    ex_date: date
    price_factor: float
    volume_factor: float
    share_factor: float
    source_action: CorporateAction


def compute_adjustment_factor(action: CorporateAction) -> AdjustmentFactor:
    """
    Compute adjustment factors from a corporate action.

    BONUS N:E  (N new shares per E existing):
        price_factor  = E / (E + N)
        volume_factor = (E + N) / E
        share_factor  = (E + N) / E

    SPLIT N:E  (N shares for every E held):
        price_factor  = E / N
        volume_factor = N / E
        share_factor  = N / E

    SYMBOL_CHANGE:
        All factors = 1.0 (no price/share change)

    DIVIDEND:
        All factors = 1.0 (price adjustment not applied in V1)
    """
    if action.action_type == "BONUS":
        n = action.ratio_new
        e = action.ratio_existing
        price_f = e / (e + n)
        vol_f = (e + n) / e
        share_f = (e + n) / e

    elif action.action_type == "SPLIT":
        n = action.ratio_new
        e = action.ratio_existing
        price_f = e / n
        vol_f = n / e
        share_f = n / e

    elif action.action_type == "SYMBOL_CHANGE":
        price_f = 1.0
        vol_f = 1.0
        share_f = 1.0

    elif action.action_type == "DIVIDEND":
        price_f = 1.0
        vol_f = 1.0
        share_f = 1.0

    elif action.action_type in UNSUPPORTED_ACTIONS:
        raise UnsupportedCorporateActionError(
            f"Unsupported corporate action '{action.action_type}' for "
            f"{action.security_symbol} on {action.ex_date}. "
            f"Version 1 does not support {action.action_type}. "
            f"This backtest cannot proceed. "
            f"Exclude this security or this date range."
        )
    else:
        raise ValueError(f"Unknown action_type: '{action.action_type}'")

    return AdjustmentFactor(
        ex_date=action.ex_date,
        price_factor=price_f,
        volume_factor=vol_f,
        share_factor=share_f,
        source_action=action,
    )


class UnsupportedCorporateActionError(Exception):
    """Raised when a corporate action is encountered that V1 cannot handle."""
    pass


# ── Price Adjuster ──────────────────────────────────────────────────

class PriceAdjuster:
    """
    Produces a research-adjusted price series from raw prices
    and corporate actions.

    Adjustment is applied backward from the most recent action:
    all prices BEFORE each ex_date are multiplied by the cumulative
    adjustment factor.

    The raw series is never modified. A new DataFrame is returned.

    An 'adjustment_factor' column is added for auditability: at any
    bar, adjusted_close = raw_close * adjustment_factor.
    """

    PRICE_COLS = ["open", "high", "low", "close"]
    VOLUME_COL = "volume"

    def adjust(
        self,
        raw_prices: pd.DataFrame,
        actions: List[CorporateAction],
    ) -> pd.DataFrame:
        """
        Produce an adjusted price DataFrame.

        Parameters
        ----------
        raw_prices : DataFrame
            Must contain [trading_date, open, high, low, close, volume].
        actions : list of CorporateAction
            Corporate actions for this security. Only BONUS and SPLIT
            actions produce actual price adjustment. DIVIDEND actions
            are logged as warnings. UNSUPPORTED actions raise errors.

        Returns
        -------
        DataFrame
            Same structure as raw_prices, with prices and volumes adjusted
            and an 'adjustment_factor' column added.

        Raises
        ------
        UnsupportedCorporateActionError
            If an unsupported action type is encountered.
        """
        df = raw_prices.copy()

        if not actions:
            df["adjustment_factor"] = 1.0
            return df

        # Validate and compute factors, sorted by ex_date descending
        # (most recent first, so we apply backward)
        factors = []
        for action in actions:
            if action.action_type in WARN_ONLY_ACTIONS:
                logger.warning(
                    "Dividend on %s for %s not modeled. "
                    "Returns are price-only.",
                    action.ex_date,
                    action.security_symbol,
                )
                continue

            factor = compute_adjustment_factor(action)
            if factor.price_factor != 1.0 or factor.volume_factor != 1.0:
                factors.append(factor)

        # Sort by ex_date descending (most recent first)
        factors.sort(key=lambda f: f.ex_date, reverse=True)

        # Initialize cumulative adjustment factor column
        df["adjustment_factor"] = 1.0

        # Apply each factor to all bars BEFORE the ex_date
        for factor in factors:
            mask = df["trading_date"] < factor.ex_date
            for col in self.PRICE_COLS:
                df.loc[mask, col] = df.loc[mask, col] * factor.price_factor
            if self.VOLUME_COL in df.columns:
                df.loc[mask, self.VOLUME_COL] = (
                    df.loc[mask, self.VOLUME_COL] * factor.volume_factor
                ).astype(int)
            df.loc[mask, "adjustment_factor"] = (
                df.loc[mask, "adjustment_factor"] * factor.price_factor
            )

        return df


# ── Corporate Action Data Source ────────────────────────────────────

class CsvCorporateActionSource:
    """
    Load corporate actions from a CSV file.

    Expected CSV columns:
        symbol, action_type, ex_date,
        ratio_new (optional), ratio_existing (optional),
        dividend_per_share (optional),
        old_symbol (optional), new_symbol (optional),
        record_date (optional)
    """

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"Corporate actions CSV not found: {self.csv_path}"
            )

    def load(self, symbol: str | None = None) -> List[CorporateAction]:
        """
        Load corporate actions, optionally filtered by symbol.

        Parameters
        ----------
        symbol : str, optional
            If provided, only return actions for this symbol.

        Returns
        -------
        list of CorporateAction, sorted by ex_date ascending.
        """
        df = pd.read_csv(self.csv_path)
        df.columns = df.columns.str.strip().str.lower()

        required = {"symbol", "action_type", "ex_date"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"Corporate actions CSV missing columns: {missing}"
            )

        if symbol is not None:
            df = df[df["symbol"].str.upper() == symbol.upper()]

        actions = []
        for _, row in df.iterrows():
            action = CorporateAction(
                security_symbol=str(row["symbol"]).upper(),
                action_type=str(row["action_type"]).upper(),
                ex_date=pd.to_datetime(row["ex_date"]).date(),
                record_date=(
                    pd.to_datetime(row["record_date"]).date()
                    if pd.notna(row.get("record_date"))
                    else None
                ),
                ratio_new=(
                    int(row["ratio_new"])
                    if pd.notna(row.get("ratio_new"))
                    else None
                ),
                ratio_existing=(
                    int(row["ratio_existing"])
                    if pd.notna(row.get("ratio_existing"))
                    else None
                ),
                dividend_per_share=(
                    float(row["dividend_per_share"])
                    if pd.notna(row.get("dividend_per_share"))
                    else None
                ),
                old_symbol=(
                    str(row["old_symbol"]).upper()
                    if pd.notna(row.get("old_symbol"))
                    else None
                ),
                new_symbol=(
                    str(row["new_symbol"]).upper()
                    if pd.notna(row.get("new_symbol"))
                    else None
                ),
            )
            actions.append(action)

        # Sort by ex_date ascending
        actions.sort(key=lambda a: a.ex_date)
        return actions
