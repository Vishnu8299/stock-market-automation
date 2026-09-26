"""
Execution simulator — walks signal DataFrame bar-by-bar and fills trades.

Key design decisions:
  1. Fill price = NEXT bar's open price, not the signal bar's close.
     This prevents look-ahead bias: a signal produced from today's data
     is acted on at tomorrow's open.
  2. Position sizing — two mutually exclusive modes:
     a. Fixed quantity (trade_qty): buys a fixed number of shares.
     b. Fixed fraction (trade_fraction): buys floor(cash * fraction / price)
        shares, recomputed at each fill.
  3. BUY only when flat (no position). SELL only when holding.
     This keeps the first version simple — no pyramiding, no shorting.
  4. The simulator records the portfolio's equity at every bar to build
     the equity curve for metrics.
"""

import math

import pandas as pd

from backtesting.core.costs import CostModel, NoCostModel
from backtesting.core.portfolio import Portfolio
from backtesting.core.signal import Signal


class ExecutionSimulator:
    """
    Event-loop backtester: iterate bars, fill on next-bar open.

    Position sizing is controlled by exactly one of two parameters:

    Parameters
    ----------
    initial_capital : float
        Starting cash.
    trade_qty : int or None
        Fixed shares to buy on each BUY signal.  Mutually exclusive
        with trade_fraction.
    trade_fraction : float or None
        Fraction of available cash to deploy on each BUY signal
        (e.g. 0.25 = 25%).  Quantity is computed as
        floor(cash * fraction / fill_price) at fill time.
        Mutually exclusive with trade_qty.
    cost_model : CostModel
        Transaction cost calculator.

    Raises
    ------
    ValueError
        If both trade_qty and trade_fraction are specified, or neither is.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        trade_qty: int | None = None,
        trade_fraction: float | None = None,
        cost_model: CostModel | None = None,
    ):
        # Validate mutually exclusive sizing modes
        if trade_qty is not None and trade_fraction is not None:
            raise ValueError(
                "Specify exactly one of trade_qty or trade_fraction, not both."
            )
        if trade_qty is None and trade_fraction is None:
            # Default to legacy behavior
            trade_qty = 10

        if trade_fraction is not None and not (0.0 < trade_fraction <= 1.0):
            raise ValueError(
                f"trade_fraction must be in (0, 1], got {trade_fraction}"
            )

        self.initial_capital = initial_capital
        self.trade_qty = trade_qty
        self.trade_fraction = trade_fraction
        self.cost_model = cost_model or NoCostModel()

    @property
    def sizing_mode(self) -> str:
        """Human-readable description of the sizing mode."""
        if self.trade_qty is not None:
            return f"fixed_{self.trade_qty}_shares"
        return f"fixed_{self.trade_fraction:.0%}_equity"

    def _compute_buy_qty(self, fill_price: float, available_cash: float) -> int:
        """
        Compute the number of shares to buy.

        For fixed-qty mode, returns trade_qty.
        For fixed-fraction mode, returns floor(cash * fraction / price),
        after reserving room for estimated transaction costs.
        """
        if self.trade_qty is not None:
            return self.trade_qty

        # Fixed fraction: deploy fraction of cash
        target_notional = available_cash * self.trade_fraction
        # Estimate cost to ensure we don't overshoot cash
        # Iterative: qty = floor(target / (price * (1 + cost_rate_approx)))
        # Use a conservative estimate: try qty, check if affordable
        qty = math.floor(target_notional / fill_price)
        if qty <= 0:
            return 0

        # Verify affordability with actual cost model
        cost = self.cost_model.calculate(fill_price, qty, "BUY")
        total_needed = fill_price * qty + cost
        while total_needed > available_cash and qty > 0:
            qty -= 1
            cost = self.cost_model.calculate(fill_price, qty, "BUY")
            total_needed = fill_price * qty + cost

        return qty

    def run(
        self,
        signal_df: pd.DataFrame,
        symbol: str,
        corporate_actions: list | None = None,
    ) -> Portfolio:
        """
        Simulate trades on `signal_df`.

        Parameters
        ----------
        signal_df : DataFrame
            Must contain [trading_date, open, high, low, close, volume, signal].
            Sorted by trading_date ascending.
        symbol : str
            Ticker being traded (for portfolio position tracking).
        corporate_actions : list of (ex_date, share_factor) tuples, optional
            Corporate-action events to apply during simulation.
            Each entry is (date, float) where share_factor is the
            multiplier for shares (e.g. 2.0 for a 1:1 bonus).

        Returns
        -------
        Portfolio
            Fully populated with trades, equity curve, and cash balance.
        """
        required = {"trading_date", "open", "close", "signal"}
        missing = required - set(signal_df.columns)
        if missing:
            raise ValueError(f"signal_df missing columns: {missing}")

        df = signal_df.sort_values("trading_date").reset_index(drop=True)
        portfolio = Portfolio(self.initial_capital)

        # Build a lookup dict for corporate actions: {ex_date: share_factor}
        ca_lookup: dict = {}
        if corporate_actions:
            for ex_date, share_factor in corporate_actions:
                ca_lookup[ex_date] = share_factor

        # Pending order from the previous bar's signal, to be filled
        # at this bar's open (next-bar execution).
        pending_action: str | None = None

        for i in range(len(df)):
            row = df.iloc[i]
            bar_date = row["trading_date"]
            bar_open = float(row["open"])
            bar_close = float(row["close"])

            # ---- Apply corporate actions on ex-date ----
            # This happens BEFORE filling orders, so a pending SELL
            # after a bonus will sell the adjusted (doubled) share count.
            if bar_date in ca_lookup:
                share_factor = ca_lookup[bar_date]
                portfolio.apply_corporate_action(symbol, share_factor)

            # ---- Fill any pending order at this bar's open ----
            if pending_action == "BUY":
                qty = self._compute_buy_qty(bar_open, portfolio.cash)
                if qty > 0:
                    cost = self.cost_model.calculate(bar_open, qty, "BUY")
                    total_needed = bar_open * qty + cost
                    if total_needed <= portfolio.cash:
                        portfolio.buy(symbol, qty, bar_open, bar_date, cost)

            elif pending_action == "SELL":
                if symbol in portfolio.positions:
                    qty = portfolio.positions[symbol].quantity
                    cost = self.cost_model.calculate(bar_open, qty, "SELL")
                    portfolio.sell(symbol, qty, bar_open, bar_date, cost)

            pending_action = None  # consumed

            # ---- Decide this bar's signal for next-bar execution ----
            sig = row["signal"]
            if sig == Signal.BUY and symbol not in portfolio.positions:
                pending_action = "BUY"
            elif sig == Signal.SELL and symbol in portfolio.positions:
                pending_action = "SELL"

            # ---- Record equity (mark to close) ----
            portfolio.record_equity(bar_date, {symbol: bar_close})

        return portfolio

