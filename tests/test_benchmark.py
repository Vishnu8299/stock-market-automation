"""
M0.2.4 Benchmark Framework Tests.

Validates all benchmark invariants specified in the approved design:
  1. Buy & Hold invariants (exactly 1 BUY, 0 SELL)
  2. Same-data invariant (identical dataset hash)
  3. Determinism (same config → identical results)
  4. Capital conservation (derived from Portfolio.buy/sell/equity)
  5. Controlled-conditions enforcement
  6. Alpha computation correctness (computed, NOT asserted positive)

Uses the existing:
  - BuyAndHold (backtesting/strategies/buy_and_hold.py)
  - SmaCrossover (backtesting/strategies/sma_crossover.py)
  - run_backtest (backtesting/core/engine.py)
  - PercentageCostModel (backtesting/core/costs.py)
  - Portfolio (backtesting/core/portfolio.py)
  - ExecutionSimulator (backtesting/core/execution.py)

Does NOT modify any of the above.
"""

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtesting.benchmark import (
    BenchmarkConfig,
    BenchmarkReport,
    BenchmarkRunner,
)
from backtesting.core.costs import NoCostModel, PercentageCostModel
from backtesting.core.data import DataFrameSource
from backtesting.core.engine import run_backtest
from backtesting.core.execution import ExecutionSimulator
from backtesting.core.portfolio import Portfolio
from backtesting.core.signal import Signal
from backtesting.strategies.buy_and_hold import BuyAndHold
from backtesting.strategies.sma_crossover import SmaCrossover


# ======================================================================
# Test data factories
# ======================================================================

def _make_trending_data(
    symbol: str = "TEST",
    start: date = date(2020, 1, 1),
    n_bars: int = 500,
    base_price: float = 1000.0,
    daily_return: float = 0.001,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a synthetic trending dataset.

    Creates an upward-trending stock with slight noise.
    Deterministic via seed for reproducibility.
    """
    np.random.seed(seed)
    dates = []
    d = start
    while len(dates) < n_bars:
        if d.weekday() < 5:  # skip weekends
            dates.append(d)
        d += timedelta(days=1)

    prices = [base_price]
    for _ in range(1, n_bars):
        ret = daily_return + np.random.normal(0, 0.01)
        prices.append(prices[-1] * (1 + ret))

    df = pd.DataFrame({
        "trading_date": dates,
        "open": [p * 0.999 for p in prices],
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [100000] * n_bars,
    })
    return df


def _make_config(
    start: date = date(2020, 1, 1),
    end: date = date(2021, 12, 31),
    **kwargs,
) -> BenchmarkConfig:
    """Create a BenchmarkConfig with defaults."""
    defaults = dict(
        symbol="TEST",
        series="EQ",
        start_date=start,
        end_date=end,
        initial_capital=1_000_000.0,
        trade_fraction=0.95,
        cost_rate=0.001,
        dataset_version="test_v1",
    )
    defaults.update(kwargs)
    return BenchmarkConfig(**defaults)


def _make_runner_and_config(n_bars=500):
    """Create a runner + config + strategy for benchmark tests."""
    data = _make_trending_data(n_bars=n_bars)
    source = DataFrameSource(data)
    start = data["trading_date"].iloc[0]
    end = data["trading_date"].iloc[-1]

    config = _make_config(start=start, end=end)
    strategy = SmaCrossover(fast_period=50, slow_period=200)
    runner = BenchmarkRunner(data_source=source)

    return runner, config, strategy


# ======================================================================
# 1. BUY & HOLD INVARIANTS
# ======================================================================

class TestBuyAndHoldInvariants:
    """Buy & Hold must produce exactly 1 BUY, 0 SELL, no re-entry."""

    def test_exactly_one_buy_signal(self):
        """BuyAndHold.generate() must produce exactly 1 BUY signal."""
        data = _make_trending_data(n_bars=300)
        bh = BuyAndHold()
        signal_df = bh.generate(data)

        buy_count = (signal_df["signal"] == Signal.BUY).sum()
        assert buy_count == 1, f"Expected 1 BUY, got {buy_count}"

    def test_zero_sell_signals(self):
        """BuyAndHold.generate() must produce 0 SELL signals."""
        data = _make_trending_data(n_bars=300)
        bh = BuyAndHold()
        signal_df = bh.generate(data)

        sell_count = (signal_df["signal"] == Signal.SELL).sum()
        assert sell_count == 0, f"Expected 0 SELL, got {sell_count}"

    def test_buy_on_first_bar(self):
        """BUY signal must be on the first bar."""
        data = _make_trending_data(n_bars=300)
        bh = BuyAndHold()
        signal_df = bh.generate(data)

        assert signal_df.iloc[0]["signal"] == Signal.BUY

    def test_hold_on_all_subsequent_bars(self):
        """All bars after bar 0 must be HOLD."""
        data = _make_trending_data(n_bars=300)
        bh = BuyAndHold()
        signal_df = bh.generate(data)

        for i in range(1, len(signal_df)):
            assert signal_df.iloc[i]["signal"] == Signal.HOLD, \
                f"Bar {i} should be HOLD, got {signal_df.iloc[i]['signal']}"

    def test_no_repeated_entry(self):
        """Buy & Hold backtest must have at most 1 BUY trade."""
        data = _make_trending_data(n_bars=300)
        source = DataFrameSource(data)

        result = run_backtest(
            data_source=source,
            signal_generator=BuyAndHold(),
            symbol="TEST",
            initial_capital=1_000_000.0,
            trade_fraction=0.95,
            cost_model=NoCostModel(),
        )

        buy_trades = [t for t in result.trade_log if t.side == "BUY"]
        assert len(buy_trades) == 1, \
            f"Expected exactly 1 BUY trade, got {len(buy_trades)}"


# ======================================================================
# 2. SAME-DATA INVARIANT
# ======================================================================

class TestSameDataInvariant:
    """Both runs must consume the exact same dataset snapshot."""

    def test_data_hash_recorded(self):
        """BenchmarkReport must include a non-empty data hash."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.data_hash != ""
        assert len(report.data_hash) == 64  # SHA256 hex

    def test_data_hash_matches_source(self):
        """Data hash must match the actual data loaded."""
        data = _make_trending_data(n_bars=500)
        source = DataFrameSource(data)
        start = data["trading_date"].iloc[0]
        end = data["trading_date"].iloc[-1]

        config = _make_config(start=start, end=end)
        strategy = SmaCrossover()
        runner = BenchmarkRunner(data_source=source)
        report = runner.run(config, strategy)

        # Compute expected hash
        loaded = source.load("TEST", start, end)
        expected_hash = hashlib.sha256(
            loaded.to_csv(index=False).encode()
        ).hexdigest()

        assert report.data_hash == expected_hash

    def test_dataset_version_recorded(self):
        """Dataset version must be recorded in the report."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.dataset_version == "test_v1"


# ======================================================================
# 3. DETERMINISM
# ======================================================================

class TestDeterminism:
    """Same config + same data → identical results."""

    def test_deterministic_returns(self):
        """Two runs with identical config produce identical returns."""
        runner, config, strategy = _make_runner_and_config()

        r1 = runner.run(config, strategy)
        r2 = runner.run(config, strategy)

        assert r1.strategy_total_return_pct == r2.strategy_total_return_pct
        assert r1.benchmark_total_return_pct == r2.benchmark_total_return_pct
        assert r1.alpha_pct == r2.alpha_pct

    def test_deterministic_trade_count(self):
        """Two runs produce the same trade count."""
        runner, config, strategy = _make_runner_and_config()

        r1 = runner.run(config, strategy)
        r2 = runner.run(config, strategy)

        assert r1.strategy_trades == r2.strategy_trades
        assert r1.benchmark_trades == r2.benchmark_trades

    def test_deterministic_trade_log(self):
        """Two runs produce identical trade logs."""
        runner, config, strategy = _make_runner_and_config()

        r1 = runner.run(config, strategy)
        r2 = runner.run(config, strategy)

        for t1, t2 in zip(r1.strategy_result.trade_log, r2.strategy_result.trade_log):
            assert t1.date == t2.date
            assert t1.price == t2.price
            assert t1.quantity == t2.quantity
            assert t1.side == t2.side
            assert t1.cost == t2.cost

    def test_deterministic_data_hash(self):
        """Two runs produce the same data hash."""
        runner, config, strategy = _make_runner_and_config()

        r1 = runner.run(config, strategy)
        r2 = runner.run(config, strategy)

        assert r1.data_hash == r2.data_hash


# ======================================================================
# 4. CAPITAL CONSERVATION
# ======================================================================

class TestCapitalConservation:
    """Derived from Portfolio.buy()/sell()/equity() actual accounting.

    Source of truth:
        buy:   cash -= (price * quantity + cost)
        sell:  cash += (price * quantity - cost)
        equity = cash + Σ(position.quantity × current_price)

    Testable invariant:
        equity + total_costs == initial_capital + (total_sold + holdings_value - total_bought)
    """

    def test_conservation_buy_and_hold_no_costs(self):
        """B&H with zero costs: equity = initial + market gain."""
        data = _make_trending_data(n_bars=300)
        source = DataFrameSource(data)

        result = run_backtest(
            data_source=source,
            signal_generator=BuyAndHold(),
            symbol="TEST",
            initial_capital=1_000_000.0,
            trade_fraction=0.95,
            cost_model=NoCostModel(),
        )

        portfolio = self._reconstruct_portfolio(result, 1_000_000.0)
        final_close = data["close"].iloc[-1]

        self._verify_conservation(portfolio, final_close, "TEST")

    def test_conservation_buy_and_hold_with_costs(self):
        """B&H with 0.1% cost: accounting identity holds."""
        data = _make_trending_data(n_bars=300)
        source = DataFrameSource(data)

        result = run_backtest(
            data_source=source,
            signal_generator=BuyAndHold(),
            symbol="TEST",
            initial_capital=1_000_000.0,
            trade_fraction=0.95,
            cost_model=PercentageCostModel(rate=0.001),
        )

        portfolio = self._reconstruct_portfolio(result, 1_000_000.0)
        final_close = data["close"].iloc[-1]

        self._verify_conservation(portfolio, final_close, "TEST")

    def test_conservation_sma_strategy(self):
        """SMA with costs: accounting identity holds across multiple trades."""
        data = _make_trending_data(n_bars=500)
        source = DataFrameSource(data)

        result = run_backtest(
            data_source=source,
            signal_generator=SmaCrossover(),
            symbol="TEST",
            initial_capital=1_000_000.0,
            trade_fraction=0.95,
            cost_model=PercentageCostModel(rate=0.001),
        )

        portfolio = self._reconstruct_portfolio(result, 1_000_000.0)
        final_close = data["close"].iloc[-1]

        self._verify_conservation(portfolio, final_close, "TEST")

    def _reconstruct_portfolio(self, result, initial_capital):
        """Reconstruct portfolio state from trade log."""
        portfolio = Portfolio(initial_capital)
        for t in result.trade_log:
            if t.side == "BUY":
                portfolio.buy(t.symbol, t.quantity, t.price, t.date, t.cost)
            elif t.side == "SELL":
                portfolio.sell(t.symbol, t.quantity, t.price, t.date, t.cost)
        return portfolio

    def _verify_conservation(self, portfolio, final_close, symbol):
        """Verify the capital conservation identity.

        From Portfolio source code:
            equity = cash + Σ(position.quantity × current_price)

        Therefore:
            equity + total_costs == initial_capital + market_gain

        where market_gain = total_sold_proceeds + holdings_value - total_bought_cost
        """
        final_equity = portfolio.equity({symbol: final_close})
        total_costs = sum(t.cost for t in portfolio.trades)

        total_bought = sum(
            t.price * t.quantity for t in portfolio.trades if t.side == "BUY"
        )
        total_sold = sum(
            t.price * t.quantity for t in portfolio.trades if t.side == "SELL"
        )
        holdings_value = sum(
            pos.quantity * final_close
            for pos in portfolio.positions.values()
        )

        lhs = final_equity + total_costs
        rhs = portfolio.initial_capital + (total_sold + holdings_value - total_bought)

        assert lhs == pytest.approx(rhs, rel=1e-6), \
            f"Conservation violated: equity({final_equity:.2f}) + costs({total_costs:.2f}) " \
            f"= {lhs:.2f} != {rhs:.2f} = initial({portfolio.initial_capital:.2f}) + " \
            f"market_gain({total_sold + holdings_value - total_bought:.2f})"


# ======================================================================
# 5. CONTROLLED-CONDITIONS ENFORCEMENT
# ======================================================================

class TestControlledConditions:
    """Both runs must use identical parameters."""

    def test_same_initial_capital(self):
        """Both runs use the same starting capital."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.strategy_result.metrics.initial_capital == \
            report.benchmark_result.metrics.initial_capital

    def test_same_date_range(self):
        """Both runs cover exactly the same date boundaries."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.strategy_result.start_date == report.benchmark_result.start_date
        assert report.strategy_result.end_date == report.benchmark_result.end_date

    def test_config_is_frozen(self):
        """BenchmarkConfig must be immutable."""
        config = _make_config()

        with pytest.raises(AttributeError):
            config.symbol = "MODIFIED"  # type: ignore

        with pytest.raises(AttributeError):
            config.initial_capital = 999  # type: ignore

    def test_benchmark_uses_buy_and_hold(self):
        """Benchmark run must use the existing BuyAndHold strategy."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        # Benchmark should be Buy_and_Hold
        assert report.benchmark_result.strategy_name == "Buy_and_Hold"

    def test_strategy_name_recorded(self):
        """Strategy name must be recorded in the result."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.strategy_result.strategy_name == "SMA_50_200"


# ======================================================================
# 6. ALPHA COMPUTATION CORRECTNESS
# ======================================================================

class TestAlphaComputation:
    """Alpha must be computed. It does NOT assert alpha > 0."""

    def test_alpha_is_computed(self):
        """Alpha must exist in the report."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        assert report.alpha_pct is not None

    def test_alpha_is_arithmetic_difference(self):
        """Alpha = strategy_return - benchmark_return."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        expected_alpha = round(
            report.strategy_total_return_pct - report.benchmark_total_return_pct,
            2,
        )
        assert report.alpha_pct == expected_alpha

    def test_alpha_does_not_assert_positive(self):
        """This test intentionally does NOT check alpha > 0.

        Alpha is a research question, not a software invariant.
        A test must not say: assert strategy_beats_benchmark.
        """
        # This docstring IS the test.
        pass


# ======================================================================
# 7. REPORT FORMATTING
# ======================================================================

class TestReportFormatting:
    """Verify the benchmark report is formatted correctly."""

    def test_report_contains_key_fields(self):
        """Formatted report must include all key metrics."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)
        text = runner.format_report(report)

        assert "BENCHMARK REPORT" in text
        assert "Total return" in text
        assert "ALPHA" in text
        assert "Avg exposure" in text
        assert "Buy & Hold" in text or "Buy \u0026 Hold" in text
        assert "Strategy" in text

    def test_report_includes_provenance(self):
        """Report must include dataset hash."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)
        text = runner.format_report(report)

        assert "Dataset hash" in text
        assert report.data_hash[:16] in text


# ======================================================================
# 8. BENCHMARK END-TO-END
# ======================================================================

class TestBenchmarkEndToEnd:
    """Full pipeline: data → benchmark → report."""

    def test_full_benchmark_run(self):
        """Complete benchmark runs without errors."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        # All fields populated
        assert report.strategy_total_return_pct is not None
        assert report.benchmark_total_return_pct is not None
        assert report.alpha_pct is not None
        assert report.data_hash != ""
        assert report.strategy_trades >= 0
        assert report.benchmark_trades >= 1  # At least the BUY

    def test_benchmark_trade_count_is_one(self):
        """Buy & Hold should have exactly 1 trade (BUY fill)."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        # ExecutionSimulator only records fills, not signals.
        # BuyAndHold produces 1 BUY signal → 1 BUY fill at bar 1's open.
        # No SELL signal ever generated → only 1 trade.
        assert report.benchmark_trades == 1

    def test_exposure_metrics_present(self):
        """Exposure metrics must be reported for both strategies."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        # Buy & Hold should have high exposure (~95%)
        assert report.benchmark_avg_exposure_pct > 0

    def test_costs_tracked(self):
        """Transaction costs must be tracked for both strategies."""
        runner, config, strategy = _make_runner_and_config()
        report = runner.run(config, strategy)

        # With cost_rate=0.001, both should incur some costs
        assert report.benchmark_total_costs > 0
        assert report.strategy_total_costs >= 0
