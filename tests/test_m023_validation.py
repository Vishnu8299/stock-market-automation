"""
M0.2.3-A — Implementation Validation (Vishnu)

Adversarial stress tests for the corporate-action hybrid architecture.
These tests specifically target edge cases that could pass isolated tests
while being mathematically incorrect in combination.

Team Lead gate requirements:
    1. Adjustment-factor mathematics (direction, ratio, sequential, ordering)
    2. OHLC invariants on adjusted prices
    3. Portfolio value conservation (economic value = 0 change)
    4. Signal + event interaction (the critical hybrid test)
    5. Volume adjustment
    6. Event-version reproducibility
    7. Raw data immutability
    8. Adjusted/raw separation
    9. Backward compatibility
    10. Event ordering edge cases
"""

import numpy as np
import pandas as pd
import pytest
from datetime import date, timedelta
from copy import deepcopy

from ingestion.corporate_actions import (
    CorporateAction,
    compute_adjustment_factors,
    compute_adjusted_prices,
    PortfolioPosition,
    apply_corporate_action_to_position,
)
from backtesting.engine import SMAStrategy, BacktestResult


# ======================================================================
# Helpers
# ======================================================================

def _dates(start: date, n: int) -> list:
    """Generate n weekday-only dates starting from start."""
    dates = []
    d = start
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        dates.append(d)
        d += timedelta(days=1)
    return dates


def _make_series_with_split(
    num_days: int = 400,
    split_day: int = 200,
    pre_split_price: float = 2500.0,
    split_ratio_from: int = 1,
    split_ratio_to: int = 5,
) -> tuple:
    """Create a price series with a known split embedded in raw prices.

    Returns (raw_df, split_event, split_date).
    Pre-split: prices ~pre_split_price
    Post-split: prices ~pre_split_price / (ratio_to/ratio_from)
    """
    np.random.seed(42)
    dates = _dates(date(2024, 1, 1), num_days)
    split_date = dates[split_day]
    ratio = split_ratio_to / split_ratio_from

    prices = []
    price = pre_split_price
    for i in range(num_days):
        if i == split_day:
            price = price / ratio  # split happens
        price *= (1 + np.random.normal(0, 0.005))
        prices.append(price)

    data = {
        "symbol": ["SPLITCO"] * num_days,
        "series": ["EQ"] * num_days,
        "trading_date": dates,
        "open": [p * (1 + np.random.uniform(-0.003, 0.003)) for p in prices],
        "high": [p * (1 + abs(np.random.normal(0, 0.005))) for p in prices],
        "low": [p * (1 - abs(np.random.normal(0, 0.005))) for p in prices],
        "close": prices,
        "volume": [1000000] * num_days,
        "traded_value": [p * 1000000 for p in prices],
    }
    df = pd.DataFrame(data)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)

    event = CorporateAction(
        security_id=1, action_type="SPLIT", ex_date=split_date,
        ratio_from=split_ratio_from, ratio_to=split_ratio_to,
    )

    return df, event, split_date


# ======================================================================
# 1. ADJUSTMENT-FACTOR MATHEMATICS
# ======================================================================

class TestAdjustmentFactorMathematics:
    """Stress-test factor direction, ratio interpretation, and sequencing."""

    def test_2_to_1_split_direction(self):
        """2:1 split → pre-split price should be HALVED (factor = 0.5)."""
        dates = _dates(date(2025, 6, 1), 10)
        split_date = dates[5]

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=split_date,
            ratio_from=1, ratio_to=2,  # 1 old → 2 new
        )
        factors = compute_adjustment_factors([event], dates)

        # Pre-split: raw ₹1000 → adjusted ₹500 (factor = 0.5)
        for d in dates:
            if d < split_date:
                assert factors[d] == pytest.approx(0.5), \
                    f"1:2 split pre-split factor should be 0.5, got {factors[d]}"
            else:
                assert factors[d] == pytest.approx(1.0)

    def test_1_to_5_split_direction(self):
        """1:5 split → pre-split price should be divided by 5 (factor = 0.2)."""
        dates = _dates(date(2025, 6, 1), 10)
        split_date = dates[5]

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=split_date,
            ratio_from=1, ratio_to=5,
        )
        factors = compute_adjustment_factors([event], dates)

        for d in dates:
            if d < split_date:
                assert factors[d] == pytest.approx(0.2), \
                    f"1:5 split factor should be 0.2, got {factors[d]}"

    def test_1_to_1_bonus_direction(self):
        """1:1 bonus → pre-bonus price should be HALVED (factor = 0.5)."""
        dates = _dates(date(2025, 6, 1), 10)
        bonus_date = dates[5]

        event = CorporateAction(
            security_id=1, action_type="BONUS", ex_date=bonus_date,
            ratio_from=1, ratio_to=1,  # 1 new for every 1 held
        )
        factors = compute_adjustment_factors([event], dates)

        for d in dates:
            if d < bonus_date:
                assert factors[d] == pytest.approx(0.5), \
                    f"1:1 bonus factor should be 0.5, got {factors[d]}"
            else:
                assert factors[d] == pytest.approx(1.0)

    def test_2_to_1_bonus_direction(self):
        """2:1 bonus → 1 new for every 2 held → factor = 2/3."""
        dates = _dates(date(2025, 6, 1), 10)
        bonus_date = dates[5]

        event = CorporateAction(
            security_id=1, action_type="BONUS", ex_date=bonus_date,
            ratio_from=2, ratio_to=1,  # 1 new for every 2 held
        )
        factors = compute_adjustment_factors([event], dates)

        expected = 2.0 / (2.0 + 1.0)  # old / (old + new) = 2/3
        for d in dates:
            if d < bonus_date:
                assert factors[d] == pytest.approx(expected, rel=1e-6), \
                    f"2:1 bonus factor should be {expected}, got {factors[d]}"
            else:
                assert factors[d] == pytest.approx(1.0)

    def test_sequential_split_then_bonus(self):
        """Split on day 5, then bonus on day 8 → cumulative factor."""
        dates = _dates(date(2025, 6, 1), 15)
        split_date = dates[5]
        bonus_date = dates[8]

        events = [
            CorporateAction(
                security_id=1, action_type="SPLIT", ex_date=split_date,
                ratio_from=1, ratio_to=2,  # factor 0.5
            ),
            CorporateAction(
                security_id=1, action_type="BONUS", ex_date=bonus_date,
                ratio_from=1, ratio_to=1,  # factor 0.5
            ),
        ]
        factors = compute_adjustment_factors(events, dates)

        for d in dates:
            if d >= bonus_date:
                assert factors[d] == pytest.approx(1.0), f"Post-all: {d}"
            elif d >= split_date:
                # Between split (inclusive) and bonus: only bonus factor
                assert factors[d] == pytest.approx(0.5), f"Between: {d}"
            else:
                # Before both: cumulative = 0.5 * 0.5 = 0.25
                assert factors[d] == pytest.approx(0.25), f"Pre-all: {d}"

    def test_sequential_bonus_then_split(self):
        """Bonus on day 5, then split on day 8 → cumulative factor.

        Crucially: order of events matters. The later event (split) is
        encountered first in backward traversal.
        """
        dates = _dates(date(2025, 6, 1), 15)
        bonus_date = dates[5]
        split_date = dates[8]

        events = [
            CorporateAction(
                security_id=1, action_type="BONUS", ex_date=bonus_date,
                ratio_from=1, ratio_to=1,  # factor 0.5
            ),
            CorporateAction(
                security_id=1, action_type="SPLIT", ex_date=split_date,
                ratio_from=1, ratio_to=2,  # factor 0.5
            ),
        ]
        factors = compute_adjustment_factors(events, dates)

        for d in dates:
            if d >= split_date:
                assert factors[d] == pytest.approx(1.0)
            elif d >= bonus_date:
                assert factors[d] == pytest.approx(0.5)
            else:
                assert factors[d] == pytest.approx(0.25)

    def test_split_plus_dividend_cumulative(self):
        """Split + dividend on different dates → cumulative in SPLIT_BONUS_DIV scope."""
        dates = _dates(date(2025, 6, 1), 15)
        split_date = dates[5]
        div_date = dates[8]
        close = 500.0

        events = [
            CorporateAction(
                security_id=1, action_type="SPLIT", ex_date=split_date,
                ratio_from=1, ratio_to=2,
            ),
            CorporateAction(
                security_id=1, action_type="DIVIDEND", ex_date=div_date,
                dividend_amount=10.0,
            ),
        ]
        close_prices = {d: close for d in dates}

        factors = compute_adjustment_factors(
            events, dates, close_prices, scope="SPLIT_BONUS_DIV",
        )

        div_factor = (close - 10.0) / close  # 0.98
        split_factor = 0.5

        for d in dates:
            if d >= div_date:
                assert factors[d] == pytest.approx(1.0)
            elif d >= split_date:
                assert factors[d] == pytest.approx(div_factor, rel=1e-6)
            else:
                assert factors[d] == pytest.approx(split_factor * div_factor, rel=1e-6)

    def test_same_date_two_events(self):
        """Two events on the same ex_date → both must apply."""
        dates = _dates(date(2025, 6, 1), 10)
        event_date = dates[5]

        events = [
            CorporateAction(
                security_id=1, action_type="SPLIT", ex_date=event_date,
                ratio_from=1, ratio_to=2,  # factor 0.5
            ),
            CorporateAction(
                security_id=1, action_type="BONUS", ex_date=event_date,
                ratio_from=1, ratio_to=1,  # factor 0.5
            ),
        ]
        factors = compute_adjustment_factors(events, dates)

        for d in dates:
            if d >= event_date:
                assert factors[d] == pytest.approx(1.0)
            else:
                # Both apply on dates before ex_date: 0.5 * 0.5 = 0.25
                assert factors[d] == pytest.approx(0.25), \
                    f"Same-date cumulative should be 0.25, got {factors[d]}"


# ======================================================================
# 2. OHLC INVARIANTS ON ADJUSTED PRICES
# ======================================================================

class TestOHLCInvariants:
    """Verify OHLC sanity is preserved after adjustment."""

    def test_adjusted_ohlc_sanity_after_split(self):
        """Adjusted High >= max(O,C,L) and Low <= min(O,C,H) for every bar."""
        df, event, _ = _make_series_with_split(num_days=50, split_day=25)

        result = compute_adjusted_prices(df, [event])

        for _, row in result.iterrows():
            assert row["adj_high"] >= row["adj_open"], \
                f"adj_high < adj_open on {row['trading_date']}"
            assert row["adj_high"] >= row["adj_close"], \
                f"adj_high < adj_close on {row['trading_date']}"
            assert row["adj_high"] >= row["adj_low"], \
                f"adj_high < adj_low on {row['trading_date']}"
            assert row["adj_low"] <= row["adj_open"], \
                f"adj_low > adj_open on {row['trading_date']}"
            assert row["adj_low"] <= row["adj_close"], \
                f"adj_low > adj_close on {row['trading_date']}"

    def test_adjusted_ohlc_sanity_after_bonus(self):
        """OHLC sanity preserved after bonus adjustment."""
        dates = _dates(date(2025, 1, 1), 50)
        np.random.seed(99)
        prices = [1000 * (1 + np.random.normal(0, 0.01)) for _ in range(50)]

        df = pd.DataFrame({
            "symbol": ["BONCO"] * 50,
            "series": ["EQ"] * 50,
            "trading_date": dates,
            "open": [p * 1.002 for p in prices],
            "high": [p * 1.015 for p in prices],
            "low": [p * 0.985 for p in prices],
            "close": prices,
            "volume": [500000] * 50,
        })
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)

        event = CorporateAction(
            security_id=1, action_type="BONUS", ex_date=dates[25],
            ratio_from=1, ratio_to=1,
        )

        result = compute_adjusted_prices(df, [event])

        violations = 0
        for _, row in result.iterrows():
            if row["adj_high"] < max(row["adj_open"], row["adj_close"], row["adj_low"]):
                violations += 1
            if row["adj_low"] > min(row["adj_open"], row["adj_close"], row["adj_high"]):
                violations += 1
        assert violations == 0, f"{violations} OHLC violations in adjusted prices"

    def test_all_four_prices_adjusted(self):
        """Open, High, Low, Close must ALL be adjusted, not just Close."""
        dates = _dates(date(2025, 6, 1), 10)
        raw_open, raw_high, raw_low, raw_close = 1000, 1020, 980, 1010

        df = pd.DataFrame({
            "symbol": ["TEST"] * 10,
            "series": ["EQ"] * 10,
            "trading_date": dates,
            "open": [raw_open] * 10,
            "high": [raw_high] * 10,
            "low": [raw_low] * 10,
            "close": [raw_close] * 10,
            "volume": [100000] * 10,
        })

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=dates[5],
            ratio_from=1, ratio_to=2,
        )

        result = compute_adjusted_prices(df, [event])
        pre = result[result["trading_date"] < dates[5]]

        for _, row in pre.iterrows():
            assert row["adj_open"] == pytest.approx(raw_open * 0.5, rel=1e-3)
            assert row["adj_high"] == pytest.approx(raw_high * 0.5, rel=1e-3)
            assert row["adj_low"] == pytest.approx(raw_low * 0.5, rel=1e-3)
            assert row["adj_close"] == pytest.approx(raw_close * 0.5, rel=1e-3)


# ======================================================================
# 3. PORTFOLIO VALUE CONSERVATION
# ======================================================================

class TestPortfolioValueConservation:
    """Economic value must be unchanged by splits and bonuses."""

    def test_split_value_conservation(self):
        """Before split: 10 × ₹2,500 = ₹25,000.
        After 1:5 split: 50 × ₹500 = ₹25,000.
        Economic value change = ₹0."""
        pre_price = 2500.0
        shares = 10
        pre_value = shares * pre_price

        pos = PortfolioPosition(
            symbol="RELIANCE", series="EQ",
            shares=shares, cost_basis=pre_value,
            entry_date=date(2025, 1, 1),
        )

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 6, 15),
            ratio_from=1, ratio_to=5,
        )

        pos, cash_flows = apply_corporate_action_to_position(pos, event)
        post_price = pre_price / 5.0
        post_value = pos.shares * post_price

        assert pos.shares == 50, "1:5 split: 10 → 50 shares"
        assert post_value == pytest.approx(pre_value), \
            f"Value conservation failed: {pre_value} → {post_value}"
        assert pos.cost_basis == pre_value, "Cost basis must not change"
        assert sum(cf.amount for cf in cash_flows) == 0.0, \
            "Split generates zero cash flow"

    def test_bonus_value_conservation(self):
        """Before bonus: 10 × ₹2,400 = ₹24,000.
        After 1:1 bonus: 20 × ₹1,200 = ₹24,000.
        Economic value change = ₹0. Cash change = ₹0."""
        pre_price = 2400.0
        shares = 10
        pre_value = shares * pre_price

        pos = PortfolioPosition(
            symbol="TCS", series="EQ",
            shares=shares, cost_basis=pre_value,
            entry_date=date(2025, 1, 1),
        )

        event = CorporateAction(
            security_id=1, action_type="BONUS", ex_date=date(2025, 6, 15),
            ratio_from=1, ratio_to=1,
        )

        pos, cash_flows = apply_corporate_action_to_position(pos, event)
        post_price = pre_price / 2.0  # bonus halves the price
        post_value = pos.shares * post_price

        assert pos.shares == 20, "1:1 bonus: 10 → 20 shares"
        assert post_value == pytest.approx(pre_value), \
            f"Value conservation failed: {pre_value} → {post_value}"
        assert pos.cost_basis == pre_value, "Cost basis unchanged"
        assert sum(cf.amount for cf in cash_flows) == 0.0, \
            "Bonus generates zero cash flow"

    def test_2_to_1_bonus_value_conservation(self):
        """2:1 bonus: 1 new for every 2 held.
        Before: 100 × ₹3,000 = ₹300,000.
        After: 150 × ₹2,000 = ₹300,000."""
        pos = PortfolioPosition(
            symbol="INFY", series="EQ",
            shares=100, cost_basis=300000.0,
            entry_date=date(2025, 1, 1),
        )

        event = CorporateAction(
            security_id=1, action_type="BONUS", ex_date=date(2025, 6, 15),
            ratio_from=2, ratio_to=1,
        )

        pos, _ = apply_corporate_action_to_position(pos, event)
        post_price = 3000.0 * (2.0 / 3.0)  # 2/(2+1)
        post_value = pos.shares * post_price

        assert pos.shares == 150, "2:1 bonus: 100 → 150 shares"
        assert post_value == pytest.approx(300000.0), \
            f"Value conservation: {post_value} != 300000"

    def test_dividend_creates_cash_not_value(self):
        """Dividend: shares unchanged, cash flow = dividend × shares.
        No economic value created — it's a transfer from equity to cash."""
        pos = PortfolioPosition(
            symbol="HDFC", series="EQ",
            shares=50, cost_basis=100000.0,
            entry_date=date(2025, 1, 1),
        )

        event = CorporateAction(
            security_id=1, action_type="DIVIDEND", ex_date=date(2025, 6, 15),
            dividend_amount=20.0, dividend_type="FINAL",
        )

        original_shares = pos.shares
        original_cost = pos.cost_basis
        pos, cash_flows = apply_corporate_action_to_position(pos, event)

        assert pos.shares == original_shares, "Shares unchanged"
        assert pos.cost_basis == original_cost, "Cost basis unchanged"
        assert len(cash_flows) == 1
        assert cash_flows[0].amount == pytest.approx(50 * 20.0)


# ======================================================================
# 4. SIGNAL + EVENT INTERACTION (THE CRITICAL HYBRID TEST)
# ======================================================================

class TestSignalEventInteraction:
    """The most important integration test — connects both halves of Hybrid C.

    Scenario:
        Day 199: raw close = ~₹2,500 (pre-split)
        Day 200: 1:5 split, raw close = ~₹500

    Must prove:
        1. Raw series: artificial discontinuity EXISTS
        2. Adjusted series: continuity RESTORED
        3. SMA: no artificial crossover from split
        4. Portfolio: shares adjusted correctly
        5. NAV: no artificial 80% loss
    """

    def test_raw_discontinuity_exists(self):
        """Raw prices must show the split cliff — this is EXPECTED."""
        df, event, split_date = _make_series_with_split(
            num_days=400, split_day=200,
            pre_split_price=2500.0, split_ratio_from=1, split_ratio_to=5,
        )

        pre_close = df[df["trading_date"] < split_date]["close"].iloc[-1]
        post_close = df[df["trading_date"] >= split_date]["close"].iloc[0]

        ratio = pre_close / post_close
        # Should be approximately 5:1 (with some noise)
        assert ratio > 3.0, \
            f"Raw prices should show a ~5x discontinuity, got ratio {ratio:.2f}"

    def test_adjusted_continuity_restored(self):
        """Adjusted prices must remove the split cliff."""
        df, event, split_date = _make_series_with_split(
            num_days=400, split_day=200,
            pre_split_price=2500.0, split_ratio_from=1, split_ratio_to=5,
        )

        result = compute_adjusted_prices(df, [event])

        pre_adj = result[result["trading_date"] < split_date]["adj_close"].iloc[-1]
        post_adj = result[result["trading_date"] >= split_date]["adj_close"].iloc[0]

        ratio = pre_adj / post_adj
        # Should be approximately 1:1 (continuous)
        assert 0.9 < ratio < 1.1, \
            f"Adjusted prices should be continuous across split, got ratio {ratio:.2f}"

    def test_sma_no_false_crossover_on_adjusted(self):
        """SMA on adjusted prices must NOT produce a false crossover at the split.

        On raw prices, the 80% price drop at the split would cause the close
        to plunge below both SMAs → false sell signal.

        On adjusted prices, there should be NO such discontinuity-induced signal.
        """
        df, event, split_date = _make_series_with_split(
            num_days=400, split_day=200,
            pre_split_price=2500.0, split_ratio_from=1, split_ratio_to=5,
        )

        adjusted = compute_adjusted_prices(df, [event])

        # Compute 50/200 SMA on adjusted prices
        adjusted["sma_50"] = adjusted["adj_close"].rolling(50).mean()
        adjusted["sma_200"] = adjusted["adj_close"].rolling(200).mean()

        # Look at the 5 bars around the split
        split_idx = adjusted[adjusted["trading_date"] == split_date].index[0]
        window = adjusted.iloc[max(0, split_idx - 2):split_idx + 3]
        window = window.dropna(subset=["sma_50", "sma_200"])

        if len(window) > 0:
            # Check that the close doesn't suddenly crash below both SMAs
            for _, row in window.iterrows():
                close_to_sma_ratio = row["adj_close"] / row["sma_200"]
                assert close_to_sma_ratio > 0.5, (
                    f"False crash detected on {row['trading_date']}: "
                    f"adj_close={row['adj_close']:.2f}, "
                    f"sma_200={row['sma_200']:.2f}, "
                    f"ratio={close_to_sma_ratio:.2f}"
                )

    def test_nav_no_artificial_loss(self):
        """Portfolio NAV must not show an artificial 80% loss at the split.

        Before split: 20 shares × ₹2,500 = ₹50,000
        After split:  100 shares × ₹500 = ₹50,000

        NAV should be approximately unchanged.
        """
        pre_price = 2500.0
        shares = 20

        pos = PortfolioPosition(
            symbol="SPLITCO", series="EQ",
            shares=shares, cost_basis=shares * pre_price,
            entry_date=date(2024, 1, 1),
        )

        nav_before = pos.shares * pre_price

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2024, 7, 1),
            ratio_from=1, ratio_to=5,
        )

        pos, _ = apply_corporate_action_to_position(pos, event)
        post_price = pre_price / 5.0
        nav_after = pos.shares * post_price

        assert pos.shares == 100
        assert nav_after == pytest.approx(nav_before), \
            f"NAV changed from {nav_before} to {nav_after} — artificial loss!"


# ======================================================================
# 5. VOLUME ADJUSTMENT
# ======================================================================

class TestVolumeAdjustment:
    """Volume must adjust inversely to price (split → more shares traded)."""

    def test_volume_inverse_adjustment_split(self):
        """1:5 split → pre-split volume should be ×5 in adjusted series."""
        dates = _dates(date(2025, 6, 1), 10)
        raw_volume = 100000

        df = pd.DataFrame({
            "symbol": ["VOL"] * 10,
            "series": ["EQ"] * 10,
            "trading_date": dates,
            "open": [1000] * 10,
            "high": [1020] * 10,
            "low": [980] * 10,
            "close": [1010] * 10,
            "volume": [raw_volume] * 10,
        })

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=dates[5],
            ratio_from=1, ratio_to=5,
        )

        result = compute_adjusted_prices(df, [event])
        pre = result[result["trading_date"] < dates[5]]

        for _, row in pre.iterrows():
            # factor = 0.2, adj_volume = volume / 0.2 = volume * 5
            expected = raw_volume * 5
            assert abs(row["adj_volume"] - expected) <= 1, \
                f"Adjusted volume should be {expected}, got {row['adj_volume']}"

    def test_volume_unchanged_post_split(self):
        """Post-split volume should be unchanged (factor = 1.0)."""
        dates = _dates(date(2025, 6, 1), 10)
        raw_volume = 100000

        df = pd.DataFrame({
            "symbol": ["VOL"] * 10,
            "series": ["EQ"] * 10,
            "trading_date": dates,
            "open": [1000] * 10,
            "high": [1020] * 10,
            "low": [980] * 10,
            "close": [1010] * 10,
            "volume": [raw_volume] * 10,
        })

        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=dates[5],
            ratio_from=1, ratio_to=5,
        )

        result = compute_adjusted_prices(df, [event])
        post = result[result["trading_date"] > dates[5]]

        for _, row in post.iterrows():
            assert row["adj_volume"] == raw_volume


# ======================================================================
# 6. EVENT-VERSION REPRODUCIBILITY
# ======================================================================

class TestEventVersionReproducibility:
    """Same event version → same adjusted prices. Always."""

    def test_same_version_same_result(self):
        """Identical event log + identical raw data = identical factors."""
        dates = _dates(date(2025, 6, 1), 20)
        events = [
            CorporateAction(
                security_id=1, action_type="SPLIT", ex_date=dates[8],
                ratio_from=1, ratio_to=5, event_version="1.0",
            ),
            CorporateAction(
                security_id=1, action_type="BONUS", ex_date=dates[5],
                ratio_from=1, ratio_to=1, event_version="1.0",
            ),
        ]

        f1 = compute_adjustment_factors(events, dates)
        f2 = compute_adjustment_factors(deepcopy(events), list(dates))

        for d in dates:
            assert f1[d] == f2[d], f"Non-deterministic on {d}: {f1[d]} != {f2[d]}"

    def test_different_event_version_flagged(self):
        """Event version is recorded — different versions should be distinguishable."""
        ev1 = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 6, 10),
            ratio_from=1, ratio_to=5, event_version="1.0",
        )
        ev2 = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 6, 10),
            ratio_from=1, ratio_to=5, event_version="2.0",
        )
        assert ev1.event_version != ev2.event_version


# ======================================================================
# 7. RAW DATA IMMUTABILITY
# ======================================================================

class TestRawDataImmutability:
    """Raw prices must NEVER be modified by any operation."""

    def test_compute_adjusted_preserves_raw(self):
        """compute_adjusted_prices must not modify the input DataFrame."""
        df, event, _ = _make_series_with_split(num_days=50, split_day=25)

        original_close = df["close"].copy()
        original_open = df["open"].copy()
        original_high = df["high"].copy()
        original_low = df["low"].copy()
        original_vol = df["volume"].copy()

        _ = compute_adjusted_prices(df, [event])

        pd.testing.assert_series_equal(df["close"], original_close)
        pd.testing.assert_series_equal(df["open"], original_open)
        pd.testing.assert_series_equal(df["high"], original_high)
        pd.testing.assert_series_equal(df["low"], original_low)
        pd.testing.assert_series_equal(df["volume"], original_vol)

    def test_portfolio_action_does_not_modify_event(self):
        """apply_corporate_action should not modify the event object."""
        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 6, 15),
            ratio_from=1, ratio_to=5,
        )
        original_ratio = event.ratio_to

        pos = PortfolioPosition(
            symbol="TEST", series="EQ",
            shares=10, cost_basis=25000.0,
            entry_date=date(2025, 1, 1),
        )

        apply_corporate_action_to_position(pos, event)
        assert event.ratio_to == original_ratio


# ======================================================================
# 8. ADJUSTED/RAW SEPARATION
# ======================================================================

class TestAdjustedRawSeparation:
    """Adjusted and raw columns must be distinct — no cross-contamination."""

    def test_adjusted_columns_separate_from_raw(self):
        """Result must have BOTH raw and adjusted columns, not overwrite."""
        df, event, _ = _make_series_with_split(num_days=20, split_day=10)

        result = compute_adjusted_prices(df, [event])

        # Raw columns preserved
        assert "close" in result.columns
        assert "open" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "volume" in result.columns

        # Adjusted columns added
        assert "adj_close" in result.columns
        assert "adj_open" in result.columns
        assert "adj_high" in result.columns
        assert "adj_low" in result.columns
        assert "adj_volume" in result.columns

        # They must differ for pre-split rows
        pre = result[result["adjustment_factor"] != 1.0]
        assert len(pre) > 0, "Should have pre-split rows"
        assert not (pre["close"] == pre["adj_close"]).all(), \
            "Raw and adjusted close should differ for pre-split rows"


# ======================================================================
# 9. BACKWARD COMPATIBILITY
# ======================================================================

class TestBackwardCompatibility:
    """Existing code must work without events / without adjusted prices."""

    def test_strategy_runs_without_events(self):
        """SMAStrategy.run() must work with events=None (M0.2 behavior)."""
        np.random.seed(42)
        dates = _dates(date(2024, 1, 1), 300)
        prices = [1000 * (1 + np.random.normal(0.001, 0.01)) for _ in range(300)]
        for i in range(1, 300):
            prices[i] = prices[i - 1] * (1 + np.random.normal(0, 0.01))

        df = pd.DataFrame({
            "symbol": ["COMPAT"] * 300,
            "series": ["EQ"] * 300,
            "trading_date": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [100000] * 300,
            "traded_value": [p * 100000 for p in prices],
        })

        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(df, events=None)

        assert isinstance(result, BacktestResult)
        assert result.events_applied == 0
        assert len(result.cash_flows) == 0

    def test_no_events_means_factor_one(self):
        """No events → all adjustment factors = 1.0 (no adjustment)."""
        dates = _dates(date(2025, 1, 1), 50)
        factors = compute_adjustment_factors([], dates)
        assert all(f == 1.0 for f in factors.values())


# ======================================================================
# 10. EVENT ORDERING EDGE CASES
# ======================================================================

class TestEventOrderingEdgeCases:
    """Edge cases around event placement relative to data boundaries."""

    def test_event_before_data_window(self):
        """Event ex_date before first trading date → factor still correct.

        If the event is outside the data, it should not be applied.
        """
        dates = _dates(date(2025, 6, 1), 10)
        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 5, 1),
            ratio_from=1, ratio_to=5,
        )

        factors = compute_adjustment_factors([event], dates)
        # Event is outside data window — should not affect factors
        assert all(f == 1.0 for f in factors.values())

    def test_event_after_data_window(self):
        """Event ex_date after last trading date → no effect."""
        dates = _dates(date(2025, 6, 1), 10)
        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=date(2025, 12, 1),
            ratio_from=1, ratio_to=5,
        )

        factors = compute_adjustment_factors([event], dates)
        assert all(f == 1.0 for f in factors.values())

    def test_event_on_first_bar(self):
        """Event on the very first trading date."""
        dates = _dates(date(2025, 6, 2), 10)
        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=dates[0],
            ratio_from=1, ratio_to=2,
        )

        factors = compute_adjustment_factors([event], dates)

        # First date is the ex_date: factor = 1.0 (price is post-split)
        assert factors[dates[0]] == pytest.approx(1.0)
        # All subsequent: factor = 1.0 (no dates before ex_date exist)
        for d in dates[1:]:
            assert factors[d] == pytest.approx(1.0)

    def test_event_on_last_bar(self):
        """Event on the very last trading date."""
        dates = _dates(date(2025, 6, 2), 10)
        event = CorporateAction(
            security_id=1, action_type="SPLIT", ex_date=dates[-1],
            ratio_from=1, ratio_to=2,
        )

        factors = compute_adjustment_factors([event], dates)

        # Last date is ex_date: factor = 1.0 (post-split)
        assert factors[dates[-1]] == pytest.approx(1.0)
        # All prior dates: factor = 0.5
        for d in dates[:-1]:
            assert factors[d] == pytest.approx(0.5)

    def test_events_input_order_does_not_matter(self):
        """Events passed in any order → same result (engine sorts internally)."""
        dates = _dates(date(2025, 6, 1), 20)

        events_asc = [
            CorporateAction(security_id=1, action_type="BONUS",
                            ex_date=dates[5], ratio_from=1, ratio_to=1),
            CorporateAction(security_id=1, action_type="SPLIT",
                            ex_date=dates[10], ratio_from=1, ratio_to=2),
        ]
        events_desc = [
            CorporateAction(security_id=1, action_type="SPLIT",
                            ex_date=dates[10], ratio_from=1, ratio_to=2),
            CorporateAction(security_id=1, action_type="BONUS",
                            ex_date=dates[5], ratio_from=1, ratio_to=1),
        ]

        f_asc = compute_adjustment_factors(events_asc, dates)
        f_desc = compute_adjustment_factors(events_desc, dates)

        for d in dates:
            assert f_asc[d] == pytest.approx(f_desc[d]), \
                f"Input order affected result on {d}: {f_asc[d]} vs {f_desc[d]}"
