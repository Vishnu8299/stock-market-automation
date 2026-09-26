"""
Metrics -- quantitative performance measures from equity curve + trade log.

All metrics are computed from two inputs:
  1. equity_curve  -- list of {trading_date, equity} dicts (from Portfolio)
  2. trade_log     -- list of Trade objects (from Portfolio)

Risk-free rate is assumed 0 for Sharpe ratio in M0.2.  A proper Indian
risk-free rate (T-bill / overnight MIBOR) can be added later.
"""

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

from backtesting.core.portfolio import Trade


@dataclass
class BacktestMetrics:
    """Container for all computed performance metrics."""
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    initial_capital: float
    final_equity: float
    # Extended metrics (M0.2.2)
    annualized_volatility_pct: float
    avg_exposure_pct: float
    turnover: float
    total_costs: float


def compute_metrics(
    equity_curve: List[dict],
    trade_log: List[Trade],
    initial_capital: float,
) -> BacktestMetrics:
    """
    Compute performance metrics from a completed backtest.

    Parameters
    ----------
    equity_curve : list of dict
        Each dict has keys 'trading_date' and 'equity'.
    trade_log : list of Trade
        All trades executed during the backtest.
    initial_capital : float
        Starting cash balance.

    Returns
    -------
    BacktestMetrics
    """
    if not equity_curve:
        return BacktestMetrics(
            total_return_pct=0.0,
            cagr_pct=0.0,
            sharpe_ratio=0.0,
            max_drawdown_pct=0.0,
            total_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            initial_capital=initial_capital,
            final_equity=initial_capital,
            annualized_volatility_pct=0.0,
            avg_exposure_pct=0.0,
            turnover=0.0,
            total_costs=0.0,
        )

    eq = pd.DataFrame(equity_curve)
    equities = eq["equity"].values.astype(float)

    # ---- Total return ----
    final_equity = equities[-1]
    total_return = (final_equity / initial_capital) - 1.0

    # ---- CAGR ----
    dates = pd.to_datetime(eq["trading_date"])
    n_days = (dates.iloc[-1] - dates.iloc[0]).days
    if n_days > 0 and final_equity > 0 and initial_capital > 0:
        years = n_days / 365.25
        cagr = (final_equity / initial_capital) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # ---- Sharpe ratio (annualized, risk-free = 0) ----
    daily_returns = np.diff(equities) / equities[:-1]
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = (np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252)
    else:
        sharpe = 0.0

    # ---- Annualized volatility ----
    if len(daily_returns) > 1:
        ann_vol = float(np.std(daily_returns) * np.sqrt(252))
    else:
        ann_vol = 0.0

    # ---- Max drawdown ----
    peak = np.maximum.accumulate(equities)
    drawdowns = (equities - peak) / peak
    max_dd = float(np.min(drawdowns))

    # ---- Trade-level metrics ----
    total_trades = len(trade_log)
    sell_trades = [t for t in trade_log if t.side == "SELL" and t.pnl is not None]

    if sell_trades:
        winners = [t for t in sell_trades if t.pnl > 0]
        win_rate = len(winners) / len(sell_trades) * 100.0

        gross_profit = sum(t.pnl for t in sell_trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in sell_trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        win_rate = 0.0
        profit_factor = 0.0

    # ---- Total transaction costs ----
    total_costs = sum(t.cost for t in trade_log)

    # ---- Average exposure % ----
    # Exposure = (equity - cash) / equity = holdings_value / equity
    # Since we don't have per-bar cash in the equity curve, we estimate
    # from the trade log: exposure changes on each buy/sell.
    # A simpler and accurate approach: compute from equity curve and
    # reconstruct cash position from trades.
    #
    # For each bar, exposure_pct = 1 - (cash / equity).
    # We reconstruct cash from trade flows.
    cash_balance = initial_capital
    cash_by_date = {}
    for t in sorted(trade_log, key=lambda x: x.date):
        if t.side == "BUY":
            cash_balance -= t.price * t.quantity + t.cost
        elif t.side == "SELL":
            cash_balance += t.price * t.quantity - t.cost
        cash_by_date[t.date] = cash_balance

    # Walk equity curve and compute exposure at each bar
    running_cash = initial_capital
    exposure_pcts = []
    for entry in equity_curve:
        d = entry["trading_date"]
        e = entry["equity"]
        if d in cash_by_date:
            running_cash = cash_by_date[d]
        if e > 0:
            exposure_pcts.append((1.0 - running_cash / e) * 100.0)
        else:
            exposure_pcts.append(0.0)

    avg_exposure = float(np.mean(exposure_pcts)) if exposure_pcts else 0.0

    # ---- Turnover ----
    # Total value traded (buys + sells) / average equity
    total_traded = sum(t.price * t.quantity for t in trade_log)
    avg_equity = float(np.mean(equities)) if len(equities) > 0 else initial_capital
    turnover = total_traded / avg_equity if avg_equity > 0 else 0.0

    return BacktestMetrics(
        total_return_pct=round(total_return * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        total_trades=total_trades,
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(profit_factor, 2),
        initial_capital=initial_capital,
        final_equity=round(final_equity, 2),
        annualized_volatility_pct=round(ann_vol * 100, 2),
        avg_exposure_pct=round(avg_exposure, 2),
        turnover=round(turnover, 4),
        total_costs=round(total_costs, 2),
    )

