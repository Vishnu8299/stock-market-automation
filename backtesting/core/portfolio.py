"""
Portfolio — position tracking, trade logging, and mark-to-market NAV.

Design:
  - `Position` tracks per-symbol state (quantity, average cost, entry date).
  - `Trade` records every fill for downstream metrics / audit.
  - `Portfolio` is the stateful container that buy/sell methods mutate.

Cash is tracked in raw units (₹ / $ / whatever the price data is in).
The portfolio does NOT know about the cost model — costs are deducted
by the caller (ExecutionSimulator) before calling buy/sell, and passed
in so the trade log records them for reporting.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional


@dataclass
class Trade:
    """Immutable record of a single fill."""
    symbol: str
    side: str           # 'BUY' or 'SELL'
    quantity: int
    price: float
    cost: float         # transaction cost applied
    date: date
    pnl: Optional[float] = None   # realized P&L (set on SELL only)


@dataclass
class Position:
    """Mutable state for an open position in one symbol."""
    symbol: str
    quantity: int
    avg_cost: float
    entry_date: date


class Portfolio:
    """
    Cash + positions + trade log.

    Parameters
    ----------
    initial_capital : float
        Starting cash balance.
    """

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash: float = initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self._equity_curve: List[dict] = []

    # ------------------------------------------------------------------ #
    # Trade execution                                                      #
    # ------------------------------------------------------------------ #

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        trade_date: date,
        cost: float = 0.0,
    ) -> Trade:
        """
        Open or add to a long position.

        Deducts (price * quantity + cost) from cash.
        Raises ValueError if insufficient cash.
        """
        total_outflow = price * quantity + cost
        if total_outflow > self.cash:
            raise ValueError(
                f"Insufficient cash: need {total_outflow:.2f}, "
                f"have {self.cash:.2f}"
            )

        self.cash -= total_outflow

        if symbol in self.positions:
            pos = self.positions[symbol]
            new_qty = pos.quantity + quantity
            pos.avg_cost = (
                (pos.avg_cost * pos.quantity + price * quantity) / new_qty
            )
            pos.quantity = new_qty
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                avg_cost=price,
                entry_date=trade_date,
            )

        trade = Trade(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            price=price,
            cost=cost,
            date=trade_date,
        )
        self.trades.append(trade)
        return trade

    def sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
        trade_date: date,
        cost: float = 0.0,
    ) -> Trade:
        """
        Reduce or close a long position.

        Adds (price * quantity - cost) to cash.
        Raises ValueError if position is insufficient.
        """
        if symbol not in self.positions:
            raise ValueError(f"No position in {symbol} to sell")
        pos = self.positions[symbol]
        if quantity > pos.quantity:
            raise ValueError(
                f"Cannot sell {quantity} shares of {symbol}, "
                f"only hold {pos.quantity}"
            )

        realized_pnl = (price - pos.avg_cost) * quantity - cost
        self.cash += price * quantity - cost

        pos.quantity -= quantity
        if pos.quantity == 0:
            del self.positions[symbol]

        trade = Trade(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            price=price,
            cost=cost,
            date=trade_date,
            pnl=realized_pnl,
        )
        self.trades.append(trade)
        return trade

    # ------------------------------------------------------------------ #
    # Corporate-action adjustment                                          #
    # ------------------------------------------------------------------ #

    def apply_corporate_action(
        self,
        symbol: str,
        share_factor: float,
    ) -> None:
        """
        Adjust a position for a corporate action (bonus or split).

        Multiplies the share quantity by share_factor and divides
        avg_cost by share_factor.  No cash changes.  No trade log
        entry (this is not a trade).

        Conservation invariant (must hold exactly):
            pre_qty * pre_avg_cost == post_qty * post_avg_cost

        Parameters
        ----------
        symbol : str
            The security to adjust.
        share_factor : float
            Multiplier for shares.  E.g. 2.0 for a 1:1 bonus or
            2-for-1 split.

        Raises
        ------
        ValueError
            If no position exists in the given symbol.
        ValueError
            If share_factor is not positive.
        """
        if share_factor <= 0:
            raise ValueError(
                f"share_factor must be positive, got {share_factor}"
            )
        if share_factor == 1.0:
            return  # No adjustment needed (e.g. symbol change, dividend)

        if symbol not in self.positions:
            return  # No position to adjust — not an error

        pos = self.positions[symbol]
        pre_cost_basis = pos.quantity * pos.avg_cost

        # Adjust shares: round to nearest integer
        # (for non-integer results from fractional ratios like 1:2 bonus)
        new_qty = round(pos.quantity * share_factor)
        if new_qty <= 0:
            raise ValueError(
                f"Corporate action would result in {new_qty} shares "
                f"for {symbol} (was {pos.quantity}, factor {share_factor})"
            )

        # Adjust avg_cost to preserve total cost basis
        new_avg_cost = pre_cost_basis / new_qty

        pos.quantity = new_qty
        pos.avg_cost = new_avg_cost

        # Verify conservation invariant
        post_cost_basis = pos.quantity * pos.avg_cost
        if abs(post_cost_basis - pre_cost_basis) > 0.01:
            raise AssertionError(
                f"Conservation invariant violated: "
                f"pre={pre_cost_basis:.2f}, post={post_cost_basis:.2f}"
            )

    # ------------------------------------------------------------------ #
    # Valuation                                                            #
    # ------------------------------------------------------------------ #

    def equity(self, current_prices: Dict[str, float]) -> float:
        """
        Mark-to-market net asset value: cash + Σ(position_qty × price).
        """
        holdings_value = sum(
            pos.quantity * current_prices.get(pos.symbol, 0.0)
            for pos in self.positions.values()
        )
        return self.cash + holdings_value

    def record_equity(self, trade_date: date, current_prices: Dict[str, float]) -> None:
        """Snapshot today's equity for the equity curve."""
        self._equity_curve.append({
            "trading_date": trade_date,
            "equity": self.equity(current_prices),
        })

    @property
    def equity_curve(self) -> List[dict]:
        return list(self._equity_curve)

    @property
    def trade_log(self) -> List[Trade]:
        return list(self.trades)
