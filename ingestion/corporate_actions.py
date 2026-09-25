"""
Corporate Action Adjustment Engine — M0.2.3 Hybrid Architecture.

Computes adjusted price series from raw prices + corporate action events.
The adjusted series is used ONLY for indicator/signal calculation (SMAs, RSI, etc.).
Portfolio accounting uses event-driven simulation on raw prices.

Adjustment factor calculation (backward from most recent date):
    factor = 1.0 at the latest date
    For each corporate action, walking backward:
        SPLIT  1:5  → factor *= (ratio_from / ratio_to) = 1/5
        BONUS  1:1  → factor *= (ratio_from / (ratio_from + ratio_to)) = 1/2
        DIVIDEND    → factor *= (close - dividend) / close  (optional)

    adjusted_price = raw_price * cumulative_factor

This matches the standard "backward adjustment" used by Bloomberg, Refinitiv,
and institutional data providers.

Team Lead decision (2026-09-25):
    - Dividend adjustment is configurable, default OFF for SMA/indicators
    - Splits + bonuses always applied
    - Raw prices never modified
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CorporateAction:
    """A single corporate action event."""
    security_id: int
    action_type: str
    ex_date: date
    ratio_from: Optional[int] = None
    ratio_to: Optional[int] = None
    dividend_amount: Optional[float] = None
    dividend_type: Optional[str] = None
    rights_price: Optional[float] = None
    rights_ratio_from: Optional[int] = None
    rights_ratio_to: Optional[int] = None
    related_security_id: Optional[int] = None
    swap_ratio_from: Optional[int] = None
    swap_ratio_to: Optional[int] = None
    new_symbol: Optional[str] = None
    event_version: str = "1.0"


@dataclass
class AdjustmentResult:
    """Result of computing adjusted prices for a security."""
    security_id: int
    symbol: str
    events_applied: List[str]
    adjustment_scope: str
    event_version: str
    raw_rows: int
    adjusted_rows: int
    adjustment_factors: dict  # date -> factor


def compute_adjustment_factors(
    events: List[CorporateAction],
    price_dates: List[date],
    close_prices: Optional[dict] = None,
    scope: str = "SPLIT_BONUS",
) -> dict:
    """Compute cumulative adjustment factor for each trading date.

    Args:
        events: Corporate actions sorted by ex_date ascending.
        price_dates: All trading dates sorted ascending.
        close_prices: {date: close_price} map, required if scope includes DIV.
        scope: 'SPLIT_BONUS' or 'SPLIT_BONUS_DIV'.

    Returns:
        {date: adjustment_factor} dict. Factor = 1.0 means no adjustment.
    """
    if not price_dates:
        return {}

    # Filter events by scope
    applicable_types = {"SPLIT", "BONUS"}
    if scope == "SPLIT_BONUS_DIV":
        applicable_types.add("DIVIDEND")

    relevant_events = [
        e for e in events
        if e.action_type in applicable_types and e.ex_date in set(price_dates)
    ]

    # Sort events by ex_date descending (we compute backward)
    relevant_events.sort(key=lambda e: e.ex_date, reverse=True)

    # Start from factor = 1.0 at the latest date, walk backward
    factors = {}
    cumulative = 1.0

    # Walk dates backward
    sorted_dates = sorted(price_dates, reverse=True)
    event_idx = 0

    for d in sorted_dates:
        # IMPORTANT: assign the factor FIRST, before processing events.
        # On the ex_date, the price is already post-split/post-bonus,
        # so the current cumulative factor (before this event's adjustment)
        # is correct for this date.  The adjustment only affects dates
        # strictly BEFORE the ex_date.
        factors[d] = cumulative

        # Then apply any events whose ex_date matches this date
        # (these adjustments will affect all subsequent dates in the
        # backward walk, i.e., dates before this one)
        while event_idx < len(relevant_events) and relevant_events[event_idx].ex_date == d:
            ev = relevant_events[event_idx]

            if ev.action_type == "SPLIT":
                if ev.ratio_from and ev.ratio_to and ev.ratio_to > 0:
                    # Pre-split prices are too high → divide by ratio
                    cumulative *= (ev.ratio_from / ev.ratio_to)
                    logger.info(
                        "SPLIT %d:%d on %s → factor now %.8f",
                        ev.ratio_from, ev.ratio_to, d, cumulative,
                    )

            elif ev.action_type == "BONUS":
                if ev.ratio_from and ev.ratio_to:
                    # Bonus 1:1 means 1 new for every 1 held → prices halve
                    old_shares = ev.ratio_from
                    new_shares = ev.ratio_from + ev.ratio_to
                    cumulative *= (old_shares / new_shares)
                    logger.info(
                        "BONUS %d:%d on %s → factor now %.8f",
                        ev.ratio_from, ev.ratio_to, d, cumulative,
                    )

            elif ev.action_type == "DIVIDEND" and scope == "SPLIT_BONUS_DIV":
                if ev.dividend_amount and close_prices and d in close_prices:
                    close = close_prices[d]
                    if close > 0 and ev.dividend_amount < close:
                        cumulative *= ((close - ev.dividend_amount) / close)
                        logger.info(
                            "DIVIDEND %.2f on %s (close=%.2f) → factor now %.8f",
                            ev.dividend_amount, d, close, cumulative,
                        )

            event_idx += 1

    return factors


def compute_adjusted_prices(
    raw_df: pd.DataFrame,
    events: List[CorporateAction],
    scope: str = "SPLIT_BONUS",
) -> pd.DataFrame:
    """Compute adjusted OHLCV prices from raw prices and corporate actions.

    Args:
        raw_df: DataFrame with columns: trading_date, open, high, low, close,
                volume. Must be sorted by trading_date ascending.
        events: List of CorporateAction objects for this security.
        scope: 'SPLIT_BONUS' (default) or 'SPLIT_BONUS_DIV'.

    Returns:
        DataFrame with additional columns: adj_open, adj_high, adj_low,
        adj_close, adj_volume, adjustment_factor.
    """
    if raw_df.empty:
        return raw_df.copy()

    dates = raw_df["trading_date"].tolist()
    close_prices = dict(zip(raw_df["trading_date"], raw_df["close"])) if scope == "SPLIT_BONUS_DIV" else None

    factors = compute_adjustment_factors(events, dates, close_prices, scope)

    result = raw_df.copy()
    result["adjustment_factor"] = result["trading_date"].map(factors).fillna(1.0)

    # Adjust prices by factor
    for col in ["open", "high", "low", "close"]:
        if col in result.columns:
            result[f"adj_{col}"] = (result[col] * result["adjustment_factor"]).round(4)

    # Volume adjusts inversely (more shares after split → volume increases)
    if "volume" in result.columns:
        result["adj_volume"] = (
            result["volume"] / result["adjustment_factor"]
        ).round(0).astype("Int64")

    result["adjustment_scope"] = scope

    return result


# ======================================================================
# Event-driven portfolio accounting — used by the backtester
# ======================================================================

@dataclass
class PortfolioPosition:
    """Tracks a single position with corporate-action-aware accounting."""
    symbol: str
    series: str
    shares: int
    cost_basis: float  # total cost (entry_price * original_shares)
    entry_date: date

    @property
    def avg_cost_per_share(self) -> float:
        if self.shares == 0:
            return 0.0
        return self.cost_basis / self.shares


@dataclass
class CashFlow:
    """A cash event generated by a corporate action."""
    date: date
    amount: float
    source: str  # 'DIVIDEND', 'RIGHTS_EXERCISE', etc.
    symbol: str


def apply_corporate_action_to_position(
    position: PortfolioPosition,
    event: CorporateAction,
) -> tuple:
    """Apply a corporate action to a portfolio position.

    Returns:
        (updated_position, cash_flows: List[CashFlow])

    The position is mutated in-place for splits/bonuses.
    Cash flows are returned for dividends.
    """
    cash_flows = []

    if event.action_type == "SPLIT":
        if event.ratio_from and event.ratio_to:
            # 1:5 split → shares * 5, cost_basis unchanged
            multiplier = event.ratio_to / event.ratio_from
            position.shares = int(position.shares * multiplier)
            # cost_basis stays the same — just spread across more shares
            logger.info(
                "SPLIT %d:%d applied to %s: shares now %d, avg cost %.2f",
                event.ratio_from, event.ratio_to, position.symbol,
                position.shares, position.avg_cost_per_share,
            )

    elif event.action_type == "BONUS":
        if event.ratio_from and event.ratio_to:
            # Bonus 1:1 → for every ratio_from shares, get ratio_to new
            bonus_shares = int(position.shares * event.ratio_to / event.ratio_from)
            position.shares += bonus_shares
            # Bonus shares have ₹0 acquisition cost — total cost_basis unchanged
            logger.info(
                "BONUS %d:%d applied to %s: +%d shares (free), total %d, avg cost %.2f",
                event.ratio_from, event.ratio_to, position.symbol,
                bonus_shares, position.shares, position.avg_cost_per_share,
            )

    elif event.action_type == "DIVIDEND":
        if event.dividend_amount:
            div_income = event.dividend_amount * position.shares
            cash_flows.append(CashFlow(
                date=event.ex_date,
                amount=div_income,
                source=f"DIVIDEND_{event.dividend_type or 'REGULAR'}",
                symbol=position.symbol,
            ))
            logger.info(
                "DIVIDEND %.2f/share on %s: %d shares × %.2f = %.2f income",
                event.dividend_amount, position.symbol,
                position.shares, event.dividend_amount, div_income,
            )

    return position, cash_flows
