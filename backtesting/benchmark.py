"""
M0.2.4 Benchmark Framework — compare any strategy against Buy & Hold
under identical controlled conditions.

Architecture (Team Lead approved 2026-09-27):

    BenchmarkConfig (frozen)
           │
           ▼
    BenchmarkRunner
     /            \
    /              \
   ▼                ▼
BuyAndHold      Strategy under test
(existing)      (e.g. SmaCrossover)
   │                │
   └──── run_backtest() ────┘
                │
                ▼
        BenchmarkReport

Rules (locked by Team Lead):
  - Do NOT create another BuyAndHold strategy — reuse existing.
  - Do NOT modify the backtesting engine.
  - Do NOT modify corporate-action logic.
  - Do NOT optimize SMA 50/200.
  - Do NOT choose evaluation period after seeing results.
  - Do NOT assert SMA must beat Buy & Hold.
  - Use the existing PercentageCostModel.
  - Use the same dataset snapshot for both runs.
  - Record the dataset hash/version.
  - Make the benchmark deterministic.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from backtesting.core.costs import CostModel, PercentageCostModel
from backtesting.core.data import DataInterface
from backtesting.core.engine import BacktestResult, run_backtest
from backtesting.core.metrics import BacktestMetrics
from backtesting.core.signal import SignalInterface
from backtesting.strategies.buy_and_hold import BuyAndHold


# ======================================================================
# BenchmarkConfig — frozen before any run
# ======================================================================

@dataclass(frozen=True)
class BenchmarkConfig:
    """Immutable benchmark configuration. Frozen before any run.

    Team Lead rules:
      - Evaluation period explicitly specified, NOT derived from data.
      - cost_rate uses PercentageCostModel semantics: applied per-fill
        (BUY and SELL separately), not per round-trip.
      - trade_fraction is a sizing convention; realized exposure is
        observed, not controlled.
    """

    # Security
    symbol: str
    series: str = "EQ"

    # Evaluation period (explicitly specified, NOT derived from data)
    start_date: date = None  # type: ignore[assignment]
    end_date: date = None  # type: ignore[assignment]

    # Capital
    initial_capital: float = 1_000_000.0

    # Sizing — same rule for both strategies.
    # Realized exposure is an observed metric, not a controlled equality.
    trade_fraction: float = 0.95

    # Cost model — uses existing PercentageCostModel from
    # backtesting/core/costs.py.
    #
    # Semantics (from source code):
    #   cost = abs(price * quantity) * rate
    #
    # Applied on EACH EXECUTED FILL (BUY and SELL separately).
    # A complete round-trip incurs costs on both legs.
    cost_rate: float = 0.001  # 0.1% per fill

    # Corporate action treatment
    adjusted: bool = True
    adjustment_scope: str = "SPLIT_BONUS"

    # Strategy parameters (for the strategy under test)
    strategy_name: str = "SMA_50_200"

    # Provenance
    dataset_version: str = ""
    config_version: str = "1.0"


# ======================================================================
# BenchmarkReport — complete comparison output
# ======================================================================

@dataclass
class BenchmarkReport:
    """Complete benchmark comparison: strategy vs Buy & Hold."""

    config: BenchmarkConfig

    # Results from the existing engine
    strategy_result: BacktestResult
    benchmark_result: BacktestResult  # Buy & Hold

    # Derived metrics
    strategy_total_return_pct: float
    benchmark_total_return_pct: float
    alpha_pct: float  # strategy - benchmark (arithmetic)

    # Exposure metrics (observed, not controlled)
    strategy_avg_exposure_pct: float
    benchmark_avg_exposure_pct: float
    strategy_trades: int
    benchmark_trades: int  # should be 1 (BUY only) or 2 (BUY+SELL if sold)

    # Risk
    strategy_max_drawdown_pct: float
    benchmark_max_drawdown_pct: float
    strategy_sharpe: float
    benchmark_sharpe: float

    # Costs
    strategy_total_costs: float
    benchmark_total_costs: float

    # Provenance
    dataset_version: str
    data_hash: str  # SHA256 of the input DataFrame


# ======================================================================
# BenchmarkRunner — the orchestrator
# ======================================================================

class BenchmarkRunner:
    """Run a strategy and Buy & Hold on the same data, same conditions.

    Uses the existing run_backtest() orchestrator and reuses the
    existing BuyAndHold signal generator. Does NOT create a new
    Buy & Hold implementation.
    """

    def __init__(self, data_source: DataInterface):
        self.data_source = data_source

    def run(
        self,
        config: BenchmarkConfig,
        strategy: SignalInterface,
        corporate_actions: list | None = None,
    ) -> BenchmarkReport:
        """Execute the benchmark comparison.

        Both the strategy and Buy & Hold are run through the same
        run_backtest() pipeline with identical:
          - data source
          - date range
          - initial capital
          - trade sizing
          - cost model
          - corporate actions

        Args:
            config: Frozen benchmark configuration.
            strategy: The strategy under test (e.g. SmaCrossover).
            corporate_actions: Optional CA events (from CorporateAction).

        Returns:
            BenchmarkReport with full comparison metrics.
        """
        # Build cost model from config rate — same instance for both
        cost_model = PercentageCostModel(rate=config.cost_rate)

        # Compute data hash for provenance — load once, hash it
        raw_data = self.data_source.load(
            symbol=config.symbol,
            start=config.start_date,
            end=config.end_date,
        )
        data_hash = hashlib.sha256(
            raw_data.to_csv(index=False).encode()
        ).hexdigest()

        # --- Run Buy & Hold (existing implementation) ---
        benchmark_result = run_backtest(
            data_source=self.data_source,
            signal_generator=BuyAndHold(),
            symbol=config.symbol,
            start=config.start_date,
            end=config.end_date,
            initial_capital=config.initial_capital,
            trade_fraction=config.trade_fraction,
            cost_model=cost_model,
            corporate_actions=corporate_actions,
        )

        # --- Run strategy under test ---
        strategy_result = run_backtest(
            data_source=self.data_source,
            signal_generator=strategy,
            symbol=config.symbol,
            start=config.start_date,
            end=config.end_date,
            initial_capital=config.initial_capital,
            trade_fraction=config.trade_fraction,
            cost_model=cost_model,
            corporate_actions=corporate_actions,
        )

        # --- Compute alpha ---
        s_ret = strategy_result.metrics.total_return_pct
        b_ret = benchmark_result.metrics.total_return_pct
        alpha = s_ret - b_ret

        return BenchmarkReport(
            config=config,
            strategy_result=strategy_result,
            benchmark_result=benchmark_result,
            strategy_total_return_pct=s_ret,
            benchmark_total_return_pct=b_ret,
            alpha_pct=round(alpha, 2),
            strategy_avg_exposure_pct=strategy_result.metrics.avg_exposure_pct,
            benchmark_avg_exposure_pct=benchmark_result.metrics.avg_exposure_pct,
            strategy_trades=strategy_result.metrics.total_trades,
            benchmark_trades=benchmark_result.metrics.total_trades,
            strategy_max_drawdown_pct=strategy_result.metrics.max_drawdown_pct,
            benchmark_max_drawdown_pct=benchmark_result.metrics.max_drawdown_pct,
            strategy_sharpe=strategy_result.metrics.sharpe_ratio,
            benchmark_sharpe=benchmark_result.metrics.sharpe_ratio,
            strategy_total_costs=strategy_result.metrics.total_costs,
            benchmark_total_costs=benchmark_result.metrics.total_costs,
            dataset_version=config.dataset_version,
            data_hash=data_hash,
        )

    def format_report(self, report: BenchmarkReport) -> str:
        """Generate a human-readable benchmark report."""
        lines = [
            "=" * 70,
            "M0.2.4 BENCHMARK REPORT",
            "=" * 70,
            "",
            f"Symbol:           {report.config.symbol}/{report.config.series}",
            f"Period:           {report.config.start_date} → {report.config.end_date}",
            f"Initial capital:  ₹{report.config.initial_capital:,.0f}",
            f"Trade fraction:   {report.config.trade_fraction:.0%}",
            f"Cost rate:        {report.config.cost_rate:.1%} per fill",
            f"Dataset hash:     {report.data_hash[:16]}...",
            "",
            "-" * 70,
            f"{'Metric':<30} {'Strategy':>15} {'Buy & Hold':>15}",
            "-" * 70,
            f"{'Total return':<30} {report.strategy_total_return_pct:>14.2f}% {report.benchmark_total_return_pct:>14.2f}%",
            f"{'CAGR':<30} {report.strategy_result.metrics.cagr_pct:>14.2f}% {report.benchmark_result.metrics.cagr_pct:>14.2f}%",
            f"{'Sharpe ratio':<30} {report.strategy_sharpe:>15.2f} {report.benchmark_sharpe:>15.2f}",
            f"{'Max drawdown':<30} {report.strategy_max_drawdown_pct:>14.2f}% {report.benchmark_max_drawdown_pct:>14.2f}%",
            f"{'Avg exposure':<30} {report.strategy_avg_exposure_pct:>14.2f}% {report.benchmark_avg_exposure_pct:>14.2f}%",
            f"{'Total trades':<30} {report.strategy_trades:>15d} {report.benchmark_trades:>15d}",
            f"{'Total costs':<30} ₹{report.strategy_total_costs:>13,.2f} ₹{report.benchmark_total_costs:>13,.2f}",
            f"{'Final equity':<30} ₹{report.strategy_result.metrics.final_equity:>13,.2f} ₹{report.benchmark_result.metrics.final_equity:>13,.2f}",
            "-" * 70,
            f"{'ALPHA':<30} {report.alpha_pct:>14.2f}%",
            "=" * 70,
            "",
            "NOTE: Alpha is an observed outcome, not a software invariant.",
            "      Exposure differences must be considered when interpreting alpha.",
        ]
        return "\n".join(lines)
