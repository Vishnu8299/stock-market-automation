"""
SMA Crossover Strategy Engine.

Implements a simple moving average crossover strategy (e.g. 50/200 SMA).
This module receives clean OHLCV data via DataInterface and produces
backtest results. It knows nothing about data sources or storage.

M0.2.3 Hybrid Architecture:
    - Signal generation uses ADJUSTED prices (continuous across splits)
    - Portfolio accounting uses EVENT-DRIVEN simulation on raw prices
    - Both modes are supported via the events parameter

Corporate action status: this engine uses data exactly as received from
the DataInterface. If the data is RAW (no corporate action adjustments),
the backtest results reflect raw prices. The engine does not apply
price adjustments itself — that responsibility belongs to the data layer.
However, the engine DOES apply portfolio-level adjustments (share count,
cost basis, cash flows) when corporate action events are provided.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """A single completed trade (entry + exit)."""
    symbol: str
    series: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    shares: int
    side: str  # "LONG" or "SHORT"

    @property
    def pnl(self) -> float:
        if self.side == "LONG":
            return (self.exit_price - self.entry_price) * self.shares
        return (self.entry_price - self.exit_price) * self.shares

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == "LONG":
            return (self.exit_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.exit_price) / self.entry_price * 100

    @property
    def holding_days(self) -> int:
        return (self.exit_date - self.entry_date).days


@dataclass
class CashFlowRecord:
    """A cash event generated during backtesting (dividends, rights, etc.)."""
    date: date
    amount: float
    source: str  # 'DIVIDEND_FINAL', 'DIVIDEND_INTERIM', etc.
    symbol: str


@dataclass
class BacktestResult:
    """Complete result of a backtest run."""
    symbol: str
    series: str
    strategy: str
    start_date: date
    end_date: date
    observations: int
    trades: List[Trade] = field(default_factory=list)
    cash_flows: List[CashFlowRecord] = field(default_factory=list)
    data_metadata: Optional[dict] = None
    events_applied: int = 0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_dividend_income(self) -> float:
        return sum(cf.amount for cf in self.cash_flows if "DIVIDEND" in cf.source)

    @property
    def total_return(self) -> float:
        """Total return including price P&L and dividend income."""
        return self.total_pnl + self.total_dividend_income

    @property
    def avg_return_pct(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.return_pct for t in self.trades) / self.total_trades


class SMAStrategy:
    """Simple Moving Average Crossover Strategy.

    Generates BUY when short SMA crosses above long SMA,
    SELL when short SMA crosses below long SMA.

    M0.2.3 Hybrid support:
        - Signal generation: uses prices as provided (should be adjusted
          for continuous SMAs across corporate actions)
        - Portfolio accounting: when events DataFrame is provided, applies
          share adjustments (splits, bonuses) and tracks cash flows (dividends)

    This strategy depends ONLY on the data it receives —
    it does NOT import, reference, or access any data source directly.
    """

    def __init__(self, short_window: int = 50, long_window: int = 200):
        if short_window >= long_window:
            raise ValueError(
                f"short_window ({short_window}) must be less than "
                f"long_window ({long_window})"
            )
        self.short_window = short_window
        self.long_window = long_window

    @property
    def name(self) -> str:
        return f"SMA_{self.short_window}_{self.long_window}"

    def run(
        self,
        df: pd.DataFrame,
        initial_capital: float = 100000.0,
        data_metadata: Optional[dict] = None,
        events: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """Run the SMA crossover strategy on the given OHLCV DataFrame.

        Args:
            df: DataFrame with columns: symbol, series, trading_date,
                open, high, low, close, volume. Must be sorted by
                trading_date ascending. Must have no duplicate
                (symbol, series, trading_date) rows.
                For M0.2.3: should be ADJUSTED prices for correct SMA signals.
            initial_capital: Starting capital for position sizing.
            data_metadata: Optional metadata from DataInterface.validate_data()
                to record provenance in the result.
            events: Optional DataFrame of corporate action events from
                DataInterface.load_corporate_events(). When provided, the
                engine applies portfolio-level adjustments:
                - SPLIT: multiply shares by ratio
                - BONUS: add bonus shares (zero cost basis)
                - DIVIDEND: credit cash to portfolio

        Returns:
            BacktestResult with all trades, cash flows, and metrics.

        Raises:
            ValueError: If df has insufficient data or wrong format.
        """
        # -- Input validation --
        required_cols = {"symbol", "series", "trading_date", "open", "high",
                         "low", "close", "volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        if df.empty:
            raise ValueError("DataFrame is empty — cannot run backtest")

        # Verify single security
        symbols = df["symbol"].unique()
        if len(symbols) != 1:
            raise ValueError(
                f"Expected exactly 1 symbol, got {len(symbols)}: {symbols}. "
                f"Use run() per-symbol, not on a multi-symbol DataFrame."
            )

        series_vals = df["series"].unique()
        if len(series_vals) != 1:
            raise ValueError(
                f"Expected exactly 1 series, got {len(series_vals)}: {series_vals}."
            )

        symbol = str(symbols[0])
        series = str(series_vals[0])

        # Verify sorted by date
        dates = df["trading_date"].tolist()
        if dates != sorted(dates):
            raise ValueError("DataFrame must be sorted by trading_date ascending")

        # Check for duplicate dates
        dups = df.duplicated(subset=["symbol", "series", "trading_date"])
        if dups.any():
            raise ValueError(
                f"{dups.sum()} duplicate (symbol, series, trading_date) rows found"
            )

        # Minimum data check
        if len(df) < self.long_window:
            raise ValueError(
                f"Need at least {self.long_window} observations for "
                f"{self.name}, got {len(df)}"
            )

        # -- Build event lookup --
        event_map = {}  # date -> list of event dicts
        events_applied = 0
        if events is not None and not events.empty:
            for _, ev in events.iterrows():
                ed = ev["ex_date"]
                if not isinstance(ed, date):
                    ed = pd.Timestamp(ed).date()
                event_map.setdefault(ed, []).append(ev.to_dict())

        # -- Compute SMAs --
        work = df.copy()
        work["sma_short"] = work["close"].rolling(window=self.short_window).mean()
        work["sma_long"] = work["close"].rolling(window=self.long_window).mean()

        # Drop rows before the long SMA is available
        work = work.dropna(subset=["sma_short", "sma_long"]).copy()

        # -- Generate signals --
        work["signal"] = 0
        work.loc[work["sma_short"] > work["sma_long"], "signal"] = 1   # bullish
        work.loc[work["sma_short"] <= work["sma_long"], "signal"] = -1  # bearish

        # Detect crossovers (signal changes)
        work["prev_signal"] = work["signal"].shift(1)
        work["crossover"] = work["signal"] != work["prev_signal"]

        # -- Simulate trades with event-driven portfolio accounting --
        trades = []
        cash_flows = []
        position = None  # None means flat

        for _, row in work.iterrows():
            td = row["trading_date"]
            if not isinstance(td, date):
                td = pd.Timestamp(td).date()

            # Apply corporate actions if we have a position
            if position is not None and td in event_map:
                for ev in event_map[td]:
                    action_type = ev.get("action_type", "")

                    if action_type == "SPLIT":
                        ratio_from = ev.get("ratio_from")
                        ratio_to = ev.get("ratio_to")
                        if ratio_from and ratio_to:
                            multiplier = ratio_to / ratio_from
                            position["shares"] = int(position["shares"] * multiplier)
                            events_applied += 1
                            logger.info(
                                "SPLIT %d:%d on %s: shares now %d",
                                ratio_from, ratio_to, td, position["shares"],
                            )

                    elif action_type == "BONUS":
                        ratio_from = ev.get("ratio_from")
                        ratio_to = ev.get("ratio_to")
                        if ratio_from and ratio_to:
                            bonus_shares = int(
                                position["shares"] * ratio_to / ratio_from
                            )
                            position["shares"] += bonus_shares
                            events_applied += 1
                            logger.info(
                                "BONUS %d:%d on %s: +%d shares, total %d",
                                ratio_from, ratio_to, td,
                                bonus_shares, position["shares"],
                            )

                    elif action_type == "DIVIDEND":
                        div_amount = ev.get("dividend_amount")
                        if div_amount:
                            income = div_amount * position["shares"]
                            cash_flows.append(CashFlowRecord(
                                date=td,
                                amount=income,
                                source=f"DIVIDEND_{ev.get('dividend_type', 'REGULAR')}",
                                symbol=symbol,
                            ))
                            events_applied += 1
                            logger.info(
                                "DIVIDEND %.2f/share on %s: %d shares = %.2f",
                                div_amount, td, position["shares"], income,
                            )

            # Process crossover signals
            if not row["crossover"]:
                continue

            if row["signal"] == 1 and position is None:
                # BUY signal — enter long
                shares = int(initial_capital // row["close"])
                if shares > 0:
                    position = {
                        "entry_date": td,
                        "entry_price": float(row["close"]),
                        "shares": shares,
                    }

            elif row["signal"] == -1 and position is not None:
                # SELL signal — exit long
                trade = Trade(
                    symbol=symbol,
                    series=series,
                    entry_date=position["entry_date"],
                    entry_price=position["entry_price"],
                    exit_date=td,
                    exit_price=float(row["close"]),
                    shares=position["shares"],
                    side="LONG",
                )
                trades.append(trade)
                position = None

        # If still in a position at the end, close at last price
        if position is not None:
            last_row = work.iloc[-1]
            last_date = last_row["trading_date"]
            if not isinstance(last_date, date):
                last_date = pd.Timestamp(last_date).date()

            trade = Trade(
                symbol=symbol,
                series=series,
                entry_date=position["entry_date"],
                entry_price=position["entry_price"],
                exit_date=last_date,
                exit_price=float(last_row["close"]),
                shares=position["shares"],
                side="LONG",
            )
            trades.append(trade)

        return BacktestResult(
            symbol=symbol,
            series=series,
            strategy=self.name,
            start_date=dates[0] if isinstance(dates[0], date) else pd.Timestamp(dates[0]).date(),
            end_date=dates[-1] if isinstance(dates[-1], date) else pd.Timestamp(dates[-1]).date(),
            observations=len(df),
            trades=trades,
            cash_flows=cash_flows,
            data_metadata=data_metadata,
            events_applied=events_applied,
        )
