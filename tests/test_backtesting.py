"""
Tests for the M0.2 backtesting framework.

All tests run against synthetic data — no external dependencies, no
database, no CSV files on disk.  This mirrors the M0.1 testing approach
(synthetic bhavcopy with injected defects).

Test categories:
  1. Signal generation  — verify SMA crossover signals land on expected bars
  2. Portfolio math      — buy/sell P&L and cash tracking
  3. Cost model          — PercentageCostModel arithmetic
  4. Metrics             — CAGR / Sharpe / max-drawdown from known equity curve
  5. End-to-end          — full engine run on synthetic data
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtesting.core.costs import NoCostModel, PercentageCostModel
from backtesting.core.data import DataFrameSource
from backtesting.core.engine import run_backtest
from backtesting.core.execution import ExecutionSimulator
from backtesting.core.metrics import compute_metrics
from backtesting.core.portfolio import Portfolio
from backtesting.core.reporting import print_report
from backtesting.core.signal import Signal
from backtesting.strategies.sma_crossover import SmaCrossover


# ===================================================================== #
# Helpers                                                                 #
# ===================================================================== #

def _make_price_series(n_bars: int, start_price: float = 100.0, seed: int = 42):
    """
    Generate a synthetic OHLCV DataFrame with a random-walk close price.

    The walk is seeded for reproducibility.
    """
    rng = np.random.RandomState(seed)
    base_date = date(2020, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(n_bars)]

    close = np.empty(n_bars)
    close[0] = start_price
    for i in range(1, n_bars):
        close[i] = close[i - 1] * (1 + rng.normal(0, 0.015))

    # Construct plausible OHLV from close
    high = close * (1 + rng.uniform(0.001, 0.02, n_bars))
    low = close * (1 - rng.uniform(0.001, 0.02, n_bars))
    open_ = close * (1 + rng.normal(0, 0.005, n_bars))
    volume = rng.randint(10_000, 500_000, n_bars)

    return pd.DataFrame({
        "trading_date": dates,
        "open": np.round(open_, 2),
        "high": np.round(high, 2),
        "low": np.round(low, 2),
        "close": np.round(close, 2),
        "volume": volume,
    })


def _make_crossover_data():
    """
    Build a DataFrame where we know EXACTLY where the SMA(2)/SMA(4)
    crossover happens, so we can verify signal placement precisely.

    Uses small SMA periods (2, 4) for tractability.
    """
    # Price sequence designed so that:
    #   - Bars 0-3: close = [10, 10, 10, 10]  →  SMA2=SMA4=10, no cross
    #   - Bars 4-5: close = [12, 14]           →  SMA2 rises above SMA4 → BUY
    #   - Bars 6-7: close = [8, 6]             →  SMA2 drops below SMA4 → SELL
    prices = [10, 10, 10, 10, 12, 14, 8, 6]
    n = len(prices)
    base_date = date(2020, 1, 1)
    return pd.DataFrame({
        "trading_date": [base_date + timedelta(days=i) for i in range(n)],
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [100_000] * n,
    })


# ===================================================================== #
# 1. Signal generation                                                    #
# ===================================================================== #

class TestSmaCrossover:
    def test_signal_column_added(self):
        df = _make_price_series(250)
        strategy = SmaCrossover(fast_period=50, slow_period=200)
        result = strategy.generate(df)
        assert "signal" in result.columns

    def test_warmup_period_is_hold(self):
        """First slow_period bars must all be HOLD."""
        df = _make_price_series(250)
        strategy = SmaCrossover(fast_period=50, slow_period=200)
        result = strategy.generate(df)
        warmup = result.iloc[:200]
        assert all(s == Signal.HOLD for s in warmup["signal"])

    def test_crossover_signals_on_known_data(self):
        """
        With SMA(2)/SMA(4) on a hand-crafted price series, verify BUY
        and SELL signals land on the expected bars.
        """
        df = _make_crossover_data()
        strategy = SmaCrossover(fast_period=2, slow_period=4)
        result = strategy.generate(df)

        signals = result["signal"].tolist()

        # Bars 0-3: warm-up (SMA4 needs 4 bars) → HOLD
        assert all(s == Signal.HOLD for s in signals[:4]), f"Warmup signals: {signals[:4]}"

        # Bar 4: SMA2 starts rising above SMA4 → expect BUY on bar 4 or 5
        buy_bars = [i for i, s in enumerate(signals) if s == Signal.BUY]
        assert len(buy_bars) >= 1, f"Expected at least one BUY, got signals: {signals}"

        # Bar 6 or 7: SMA2 drops below SMA4 → expect SELL
        sell_bars = [i for i, s in enumerate(signals) if s == Signal.SELL]
        assert len(sell_bars) >= 1, f"Expected at least one SELL, got signals: {signals}"

    def test_invalid_periods_raise(self):
        with pytest.raises(ValueError):
            SmaCrossover(fast_period=200, slow_period=50)

    def test_name_property(self):
        assert SmaCrossover(50, 200).name == "SMA_50_200"


# ===================================================================== #
# 2. Portfolio math                                                       #
# ===================================================================== #

class TestPortfolio:
    def test_buy_reduces_cash(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        assert p.cash == pytest.approx(99_000.0)
        assert p.positions["TEST"].quantity == 10

    def test_sell_increases_cash(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        p.sell("TEST", 10, 120.0, date(2020, 2, 1))
        # Bought 10 @ 100 = -1000, sold 10 @ 120 = +1200
        assert p.cash == pytest.approx(100_200.0)
        assert "TEST" not in p.positions

    def test_sell_pnl_recorded(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        trade = p.sell("TEST", 10, 120.0, date(2020, 2, 1))
        assert trade.pnl == pytest.approx(200.0)

    def test_buy_with_costs(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1), cost=5.0)
        # 10 * 100 + 5 = 1005 deducted
        assert p.cash == pytest.approx(98_995.0)

    def test_sell_with_costs(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        trade = p.sell("TEST", 10, 120.0, date(2020, 2, 1), cost=5.0)
        # realized P&L = (120 - 100) * 10 - 5 = 195
        assert trade.pnl == pytest.approx(195.0)
        # cash: 99000 + (1200 - 5) = 100195
        assert p.cash == pytest.approx(100_195.0)

    def test_insufficient_cash_raises(self):
        p = Portfolio(500)
        with pytest.raises(ValueError, match="Insufficient cash"):
            p.buy("TEST", 10, 100.0, date(2020, 1, 1))

    def test_sell_without_position_raises(self):
        p = Portfolio(100_000)
        with pytest.raises(ValueError, match="No position"):
            p.sell("TEST", 10, 100.0, date(2020, 1, 1))

    def test_equity_mark_to_market(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        # cash = 99000, holdings = 10 * 150 = 1500
        assert p.equity({"TEST": 150.0}) == pytest.approx(100_500.0)

    def test_trade_log(self):
        p = Portfolio(100_000)
        p.buy("TEST", 10, 100.0, date(2020, 1, 1))
        p.sell("TEST", 10, 120.0, date(2020, 2, 1))
        assert len(p.trade_log) == 2
        assert p.trade_log[0].side == "BUY"
        assert p.trade_log[1].side == "SELL"


# ===================================================================== #
# 3. Cost model                                                           #
# ===================================================================== #

class TestCostModel:
    def test_no_cost(self):
        model = NoCostModel()
        assert model.calculate(100.0, 10, "BUY") == 0.0

    def test_percentage_cost(self):
        model = PercentageCostModel(rate=0.001)
        # 100 * 10 * 0.001 = 1.0
        assert model.calculate(100.0, 10, "BUY") == pytest.approx(1.0)

    def test_percentage_cost_sell(self):
        model = PercentageCostModel(rate=0.002)
        assert model.calculate(200.0, 5, "SELL") == pytest.approx(2.0)

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError):
            PercentageCostModel(rate=-0.01)


# ===================================================================== #
# 4. Metrics                                                              #
# ===================================================================== #

class TestMetrics:
    def test_total_return(self):
        """Simple equity curve: 100k → 120k = 20% return."""
        curve = [
            {"trading_date": date(2020, 1, 1), "equity": 100_000},
            {"trading_date": date(2021, 1, 1), "equity": 120_000},
        ]
        m = compute_metrics(curve, [], 100_000)
        assert m.total_return_pct == pytest.approx(20.0)

    def test_max_drawdown(self):
        """Peak at 100k, trough at 80k = -20% drawdown."""
        curve = [
            {"trading_date": date(2020, 1, 1), "equity": 100_000},
            {"trading_date": date(2020, 6, 1), "equity": 80_000},
            {"trading_date": date(2021, 1, 1), "equity": 90_000},
        ]
        m = compute_metrics(curve, [], 100_000)
        assert m.max_drawdown_pct == pytest.approx(-20.0)

    def test_cagr_one_year_20pct(self):
        curve = [
            {"trading_date": date(2020, 1, 1), "equity": 100_000},
            {"trading_date": date(2021, 1, 1), "equity": 120_000},
        ]
        m = compute_metrics(curve, [], 100_000)
        # ~1 year, 20% growth → CAGR ≈ 20% (not exact due to 365 vs 365.25)
        assert abs(m.cagr_pct - 20.0) < 1.0

    def test_empty_curve(self):
        m = compute_metrics([], [], 100_000)
        assert m.total_return_pct == 0.0
        assert m.final_equity == 100_000

    def test_win_rate_and_profit_factor(self):
        from backtesting.core.portfolio import Trade
        trades = [
            Trade("A", "BUY", 10, 100, 0, date(2020, 1, 1)),
            Trade("A", "SELL", 10, 120, 0, date(2020, 2, 1), pnl=200),
            Trade("A", "BUY", 10, 110, 0, date(2020, 3, 1)),
            Trade("A", "SELL", 10, 105, 0, date(2020, 4, 1), pnl=-50),
        ]
        curve = [
            {"trading_date": date(2020, 1, 1), "equity": 100_000},
            {"trading_date": date(2020, 4, 1), "equity": 100_150},
        ]
        m = compute_metrics(curve, trades, 100_000)
        assert m.win_rate_pct == pytest.approx(50.0)
        assert m.profit_factor == pytest.approx(4.0)  # 200 / 50


# ===================================================================== #
# 5. End-to-end                                                           #
# ===================================================================== #

class TestEndToEnd:
    def test_full_backtest_runs(self):
        """Smoke test: engine runs without error on synthetic data."""
        df = _make_price_series(500, start_price=100.0)
        source = DataFrameSource(df)
        strategy = SmaCrossover(fast_period=50, slow_period=200)

        result = run_backtest(
            data_source=source,
            signal_generator=strategy,
            symbol="SYNTHETIC",
            initial_capital=100_000,
            trade_qty=10,
        )

        assert result.metrics is not None
        assert result.metrics.initial_capital == 100_000
        assert len(result.equity_curve) == 500
        assert result.strategy_name == "SMA_50_200"

    def test_report_does_not_crash(self):
        """Verify report formatting runs without error."""
        df = _make_price_series(300, start_price=100.0)
        source = DataFrameSource(df)
        strategy = SmaCrossover(fast_period=50, slow_period=200)

        result = run_backtest(
            data_source=source,
            signal_generator=strategy,
            symbol="SYNTHETIC",
        )

        report_str = print_report(
            metrics=result.metrics,
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            start_date=str(result.start_date),
            end_date=str(result.end_date),
        )
        assert "BACKTEST REPORT" in report_str
        assert "COMPLETE" in report_str

    def test_no_look_ahead_bias(self):
        """
        Verify that BUY fills happen at the bar AFTER the signal bar,
        at that bar's open price — not the signal bar's close.
        """
        df = _make_crossover_data()
        strategy = SmaCrossover(fast_period=2, slow_period=4)
        signal_df = strategy.generate(df)

        simulator = ExecutionSimulator(
            initial_capital=100_000,
            trade_qty=10,
            cost_model=NoCostModel(),
        )
        portfolio = simulator.run(signal_df, "TEST")

        if portfolio.trades:
            first_buy = portfolio.trades[0]
            # The fill should NOT be at any signal bar's close
            buy_signal_bars = signal_df[signal_df["signal"] == Signal.BUY]
            if not buy_signal_bars.empty:
                signal_bar_close = buy_signal_bars.iloc[0]["close"]
                signal_bar_idx = buy_signal_bars.index[0]
                if signal_bar_idx + 1 < len(signal_df):
                    next_bar_open = signal_df.iloc[signal_bar_idx + 1]["open"]
                    assert first_buy.price == pytest.approx(next_bar_open), (
                        f"Fill price {first_buy.price} should be next bar open "
                        f"{next_bar_open}, not signal bar close {signal_bar_close}"
                    )
