"""
Engine -- top-level orchestrator for a single-symbol backtest.

Wires together: DataInterface -> SignalInterface -> ExecutionSimulator
-> compute_metrics -> BacktestResult.

Usage:
    result = run_backtest(
        data_source=CsvDataSource("prices.csv"),
        signal_generator=SmaCrossover(),
        cost_model=PercentageCostModel(0.001),
        symbol="RELIANCE",
        start=date(2020, 1, 1),
        end=date(2025, 12, 31),
    )
"""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from backtesting.core.adjustment import (
    CorporateAction,
    PriceAdjuster,
    compute_adjustment_factor,
)
from backtesting.core.costs import CostModel, PercentageCostModel
from backtesting.core.data import DataInterface
from backtesting.core.execution import ExecutionSimulator
from backtesting.core.metrics import BacktestMetrics, compute_metrics
from backtesting.core.portfolio import Trade
from backtesting.core.signal import SignalInterface


@dataclass
class BacktestResult:
    """Everything produced by a single backtest run."""
    metrics: BacktestMetrics
    equity_curve: List[dict]
    trade_log: List[Trade]
    strategy_name: str
    symbol: str
    start_date: date
    end_date: date


def run_backtest(
    data_source: DataInterface,
    signal_generator: SignalInterface,
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    initial_capital: float = 100_000.0,
    trade_qty: int | None = None,
    trade_fraction: float | None = None,
    cost_model: Optional[CostModel] = None,
    corporate_actions: List[CorporateAction] | None = None,
) -> BacktestResult:
    """
    Run a complete single-symbol backtest.

    Parameters
    ----------
    data_source : DataInterface
        Where to load OHLCV data from.
    signal_generator : SignalInterface
        Strategy that produces BUY/SELL/HOLD signals.
    symbol : str
        Ticker to trade.
    start, end : date, optional
        Date range for the backtest.
    initial_capital : float
        Starting cash.
    trade_qty : int, optional
        Fixed shares per BUY signal.  Mutually exclusive with trade_fraction.
        If neither is specified, defaults to 10 (legacy behavior).
    trade_fraction : float, optional
        Fraction of available cash to deploy per BUY signal (e.g. 0.25).
        Mutually exclusive with trade_qty.
    cost_model : CostModel, optional
        Transaction cost model (defaults to 0.1% PercentageCostModel).
    corporate_actions : list of CorporateAction, optional
        Corporate-action events for this security.  If provided:
        1. Prices are adjusted before signal generation (indicators
           see a continuous series).

        When prices are adjusted, portfolio share adjustment is NOT
        needed — the adjusted series is continuous, so 10 shares at
        adjusted prices correctly tracks economic value.  Share
        adjustment (Approach B) would only be needed if the simulator
        ran on raw prices, which we do not do.

    Returns
    -------
    BacktestResult
    """
    if cost_model is None:
        cost_model = PercentageCostModel(rate=0.001)

    # 1. Load data
    data = data_source.load(symbol, start, end)

    # 2. Adjust prices for indicators and fills (if corporate actions provided)
    # NOTE: When prices are adjusted, portfolio share adjustment is NOT
    # applied.  The adjusted series is continuous, so the portfolio's
    # position (10 shares × adjusted_price) correctly represents economic
    # value throughout.  Applying BOTH price adjustment AND share
    # adjustment would double-count the corporate action.
    if corporate_actions:
        adjuster = PriceAdjuster()
        adjusted_data = adjuster.adjust(data, corporate_actions)
    else:
        adjusted_data = data

    # 3. Generate signals on ADJUSTED data
    signal_df = signal_generator.generate(adjusted_data)

    # 4. Simulate execution on adjusted prices (no portfolio adjustment needed)
    simulator = ExecutionSimulator(
        initial_capital=initial_capital,
        trade_qty=trade_qty,
        trade_fraction=trade_fraction,
        cost_model=cost_model,
    )
    portfolio = simulator.run(signal_df, symbol)

    # 5. Compute metrics
    metrics = compute_metrics(
        equity_curve=portfolio.equity_curve,
        trade_log=portfolio.trade_log,
        initial_capital=initial_capital,
    )

    actual_start = data["trading_date"].iloc[0]
    actual_end = data["trading_date"].iloc[-1]

    return BacktestResult(
        metrics=metrics,
        equity_curve=portfolio.equity_curve,
        trade_log=portfolio.trade_log,
        strategy_name=signal_generator.name,
        symbol=symbol,
        start_date=actual_start,
        end_date=actual_end,
    )

