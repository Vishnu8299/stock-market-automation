"""
backtesting.core — component interfaces and implementations.

Exports the ABC interfaces (DataInterface, SignalInterface, CostModel)
and concrete implementations so strategies and scripts can import from
a single place.
"""

from backtesting.core.adjustment import (  # noqa: F401
    AdjustmentFactor,
    CorporateAction,
    CsvCorporateActionSource,
    PriceAdjuster,
    UnsupportedCorporateActionError,
    compute_adjustment_factor,
)
from backtesting.core.costs import CostModel, NoCostModel, PercentageCostModel  # noqa: F401
from backtesting.core.data import CsvDataSource, DataFrameSource, DataInterface  # noqa: F401
from backtesting.core.engine import BacktestResult, run_backtest  # noqa: F401
from backtesting.core.execution import ExecutionSimulator  # noqa: F401
from backtesting.core.metrics import BacktestMetrics, compute_metrics  # noqa: F401
from backtesting.core.portfolio import Portfolio, Position, Trade  # noqa: F401
from backtesting.core.reporting import print_report  # noqa: F401
from backtesting.core.signal import Signal, SignalInterface  # noqa: F401
