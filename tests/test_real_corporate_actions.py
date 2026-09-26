"""
M0.2.3-B — Real NSE Corporate-Action Validation Tests.

Author: Bharath Kumar Choppa
Date: 2026-09-25

These tests validate the corporate-action engine against REAL NSE events
using price data sourced from yfinance (with RAW prices reconstructed
where yfinance pre-adjusts).

Events tested:
  A. IRCTC 5:1 Stock Split  (NSE ex-date 2021-10-29)
  B. TCS 1:1 Bonus Issue    (NSE ex-date 2018-06-14)
  C. INFY Rs.17.50 Dividend  (NSE ex-date 2023-06-02)

Each event validates 5 layers:
  1. NSE source -> CorporateAction parsing
  2. CorporateAction -> AdjustmentFactor computation
  3. AdjustmentFactor -> Adjusted OHLCV prices
  4. Portfolio position adjustment (shares, avg_cost, cash)
  5. NAV / equity-curve continuity
"""

import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from backtesting.core.adjustment import (
    AdjustmentFactor,
    CorporateAction,
    PriceAdjuster,
    UnsupportedCorporateActionError,
    compute_adjustment_factor,
)
from backtesting.core.costs import NoCostModel, PercentageCostModel
from backtesting.core.data import DataFrameSource
from backtesting.core.engine import run_backtest
from backtesting.core.execution import ExecutionSimulator
from backtesting.core.portfolio import Portfolio
from backtesting.core.signal import Signal
from backtesting.strategies.buy_and_hold import BuyAndHold

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "corporate_actions"


# ════════════════════════════════════════════════════════════════
# Helper: Load fixture CSV
# ════════════════════════════════════════════════════════════════

def load_fixture(filename: str) -> pd.DataFrame:
    """Load a fixture CSV and normalize column types."""
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    df = pd.read_csv(path)
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    return df


# ════════════════════════════════════════════════════════════════
# EVENT A: IRCTC 5:1 Stock Split
# NSE: Face value changed from Rs.10 to Rs.2
# Ex-date: 2021-10-29
# ════════════════════════════════════════════════════════════════


class TestRealSplitIRCTC:
    """
    Validate the engine against IRCTC's 5:1 stock split.

    NSE source: IRCTC (Indian Railway Catering and Tourism Corporation)
    Event: Stock split, face value Rs.10 -> Rs.2 (5:1)
    Ex-date: 2021-10-29
    Pre-split close (Oct 28): ~Rs.4,567.50 (raw)
    Post-split close (Oct 29): ~Rs.845.70 (raw)
    """

    # ── Layer 1: NSE Source -> CorporateAction ──

    def test_layer1_corporate_action_parsing(self):
        """Verify CorporateAction correctly represents the IRCTC 5:1 split."""
        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )
        assert ca.security_symbol == "IRCTC"
        assert ca.action_type == "SPLIT"
        assert ca.ex_date == date(2021, 10, 29)
        assert ca.ratio_new == 5
        assert ca.ratio_existing == 1

    # ── Layer 2: CorporateAction -> AdjustmentFactor ──

    def test_layer2_adjustment_factor(self):
        """Verify computed factors for IRCTC 5:1 split."""
        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )
        factor = compute_adjustment_factor(ca)

        # 5:1 split: price_factor = 1/5 = 0.2
        assert factor.price_factor == pytest.approx(0.2)
        # volume_factor = 5/1 = 5.0
        assert factor.volume_factor == pytest.approx(5.0)
        # share_factor = 5/1 = 5.0
        assert factor.share_factor == pytest.approx(5.0)

    # ── Layer 3: Adjusted OHLCV ──

    def test_layer3_adjusted_prices_continuous(self):
        """
        Verify adjusted series has no artificial price jump at ex-date.

        RAW data: Oct 28 close ~4,567, Oct 29 close ~846
        ADJUSTED: Oct 28 close should be ~913 (4567/5), Oct 29 stays ~846
        The adjusted series should be continuous.
        """
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        # Find the pre-split and ex-date rows
        pre_split = adjusted[adjusted["trading_date"] == date(2021, 10, 28)]
        ex_date_row = adjusted[adjusted["trading_date"] == date(2021, 10, 29)]
        post_split = adjusted[adjusted["trading_date"] == date(2021, 11, 1)]

        assert len(pre_split) == 1, "Missing Oct 28 data"
        assert len(ex_date_row) == 1, "Missing Oct 29 data"
        assert len(post_split) == 1, "Missing Nov 1 data"

        pre_close = pre_split.iloc[0]["close"]
        ex_close = ex_date_row.iloc[0]["close"]
        post_close = post_split.iloc[0]["close"]

        # Pre-split adjusted close should be raw/5
        # Raw close Oct 28: ~4567.50, adjusted: ~913.50
        assert pre_close == pytest.approx(4567.5 / 5, rel=0.01), (
            f"Adjusted pre-split close: {pre_close:.2f}, "
            f"expected ~{4567.5/5:.2f}"
        )

        # Ex-date close should remain unchanged (post-action scale)
        assert ex_close == pytest.approx(845.70, rel=0.01)

        # No artificial jump: pre-split adjusted close should be within
        # reasonable trading range of ex-date close (not 5x different)
        ratio = pre_close / ex_close
        assert 0.5 < ratio < 2.0, (
            f"Artificial price jump detected: pre={pre_close:.2f}, "
            f"ex={ex_close:.2f}, ratio={ratio:.2f}"
        )

        # Adjustment factor column
        assert pre_split.iloc[0]["adjustment_factor"] == pytest.approx(0.2)
        assert ex_date_row.iloc[0]["adjustment_factor"] == pytest.approx(1.0)
        assert post_split.iloc[0]["adjustment_factor"] == pytest.approx(1.0)

    def test_layer3_ex_date_boundary(self):
        """
        Verify adjustment applies to exactly the right dates.

        Previous trading day: adjusted historical scale (factor 0.2)
        Ex-date: post-action scale (factor 1.0)
        Following day: same post-action scale (factor 1.0)
        """
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        # Check every row's adjustment factor
        for _, row in adjusted.iterrows():
            td = row["trading_date"]
            af = row["adjustment_factor"]
            if td < date(2021, 10, 29):
                assert af == pytest.approx(0.2), (
                    f"Date {td}: expected factor 0.2, got {af}"
                )
            else:
                assert af == pytest.approx(1.0), (
                    f"Date {td}: expected factor 1.0, got {af}"
                )

    # ── Layer 4: Portfolio conservation ──

    def test_layer4_portfolio_conservation(self):
        """
        Verify portfolio conservation through IRCTC 5:1 split.

        Before: 10 shares * avg_cost
        After:  50 shares * (avg_cost / 5)
        Total cost basis unchanged. Cash unchanged.
        """
        portfolio = Portfolio(initial_capital=500_000.0)

        # Buy 10 shares at Rs.4,500 (raw pre-split price)
        buy_price = 4500.0
        portfolio.buy("IRCTC", 10, buy_price, date(2021, 10, 25))

        pre_cost_basis = 10 * 4500.0
        pre_cash = portfolio.cash

        # Apply the split
        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )
        factor = compute_adjustment_factor(ca)
        portfolio.apply_corporate_action("IRCTC", factor.share_factor)

        pos = portfolio.positions["IRCTC"]

        # Shares: 10 * 5 = 50
        assert pos.quantity == 50, f"Expected 50 shares, got {pos.quantity}"

        # avg_cost: 4500 / 5 = 900
        assert pos.avg_cost == pytest.approx(900.0), (
            f"Expected avg_cost 900.0, got {pos.avg_cost:.2f}"
        )

        # Conservation invariant
        post_cost_basis = pos.quantity * pos.avg_cost
        assert post_cost_basis == pytest.approx(pre_cost_basis), (
            f"Cost basis changed: pre={pre_cost_basis}, post={post_cost_basis}"
        )

        # Cash unchanged
        assert portfolio.cash == pytest.approx(pre_cash), (
            f"Cash changed through split: pre={pre_cash}, post={portfolio.cash}"
        )

    # ── Layer 5: NAV / equity curve ──

    def test_layer5_nav_continuity(self):
        """
        Run a full backtest on IRCTC through the split.
        NAV should not have a phantom jump on ex-date.
        """
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC",
            action_type="SPLIT",
            ex_date=date(2021, 10, 29),
            ratio_new=5,
            ratio_existing=1,
        )

        result = run_backtest(
            data_source=DataFrameSource(raw_df),
            signal_generator=BuyAndHold(),
            symbol="IRCTC",
            trade_qty=10,
            initial_capital=500_000.0,
            cost_model=NoCostModel(),
            corporate_actions=[ca],
        )

        curve = result.equity_curve
        # Find equity around ex-date
        for i in range(1, len(curve)):
            td = curve[i]["trading_date"]
            prev_eq = curve[i - 1]["equity"]
            curr_eq = curve[i]["equity"]
            if prev_eq > 0:
                daily_pct = (curr_eq - prev_eq) / prev_eq
                # No single-day change should exceed 20%
                # (A phantom 5x jump = 400% change would be caught)
                assert abs(daily_pct) < 0.20, (
                    f"Excessive equity change on {td}: {daily_pct:.2%} "
                    f"(prev={prev_eq:.2f}, curr={curr_eq:.2f})"
                )


# ════════════════════════════════════════════════════════════════
# EVENT B: TCS 1:1 Bonus Issue
# NSE ex-date: 2018-06-14
# ════════════════════════════════════════════════════════════════


class TestRealBonusTCS:
    """
    Validate the engine against TCS's 1:1 bonus issue.

    NSE source: TCS (Tata Consultancy Services)
    Event: 1:1 bonus (1 new share for every 1 held)
    Ex-date: 2018-06-14
    Pre-bonus close (Jun 13): ~Rs.3,648.20 (raw, reconstructed)
    Post-bonus close (Jun 14): ~Rs.1,787.55 (raw)

    DATA SOURCING NOTE: yfinance reports this bonus as a "split" event
    on 2018-05-31 (not the actual NSE ex-date of 2018-06-14). The
    yfinance Close column is already adjusted. RAW prices are
    reconstructed by multiplying pre-ex-date prices by 2.

    This ex-date discrepancy (yfinance vs NSE) is itself a finding.
    """

    # ── Layer 1: Parsing ──

    def test_layer1_corporate_action_parsing(self):
        """Verify CorporateAction for TCS 1:1 bonus."""
        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )
        assert ca.action_type == "BONUS"
        assert ca.ratio_new == 1
        assert ca.ratio_existing == 1

    # ── Layer 2: Factor computation ──

    def test_layer2_adjustment_factor(self):
        """Verify computed factors for TCS 1:1 bonus."""
        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )
        factor = compute_adjustment_factor(ca)

        # 1:1 bonus: price_factor = 1/(1+1) = 0.5
        assert factor.price_factor == pytest.approx(0.5)
        assert factor.volume_factor == pytest.approx(2.0)
        assert factor.share_factor == pytest.approx(2.0)

    # ── Layer 3: Adjusted prices ──

    def test_layer3_adjusted_prices_continuous(self):
        """
        Verify adjusted TCS series is continuous through bonus.

        RAW: Jun 13 close ~3648, Jun 14 close ~1788
        ADJUSTED: Jun 13 close should be ~1824 (3648/2), Jun 14 stays ~1788
        """
        raw_df = load_fixture("tcs_bonus_2018_raw.csv")

        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        pre_bonus = adjusted[adjusted["trading_date"] == date(2018, 6, 13)]
        ex_date_row = adjusted[adjusted["trading_date"] == date(2018, 6, 14)]

        assert len(pre_bonus) == 1, "Missing Jun 13 data"
        assert len(ex_date_row) == 1, "Missing Jun 14 data"

        pre_close = pre_bonus.iloc[0]["close"]
        ex_close = ex_date_row.iloc[0]["close"]

        # Adjusted pre-bonus close: ~3648.20 / 2 = ~1824.10
        assert pre_close == pytest.approx(1824.10, rel=0.01), (
            f"Adjusted pre-bonus close: {pre_close:.2f}, expected ~1824.10"
        )

        # No artificial jump
        ratio = pre_close / ex_close
        assert 0.8 < ratio < 1.2, (
            f"Adjusted series not continuous: pre={pre_close:.2f}, "
            f"ex={ex_close:.2f}, ratio={ratio:.2f}"
        )

    def test_layer3_ex_date_boundary(self):
        """Verify factor boundary for TCS bonus."""
        raw_df = load_fixture("tcs_bonus_2018_raw.csv")

        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        for _, row in adjusted.iterrows():
            td = row["trading_date"]
            af = row["adjustment_factor"]
            if td < date(2018, 6, 14):
                assert af == pytest.approx(0.5), (
                    f"Date {td}: expected factor 0.5, got {af}"
                )
            else:
                assert af == pytest.approx(1.0), (
                    f"Date {td}: expected factor 1.0, got {af}"
                )

    # ── Layer 4: Portfolio conservation ──

    def test_layer4_portfolio_conservation(self):
        """
        Verify portfolio conservation through TCS 1:1 bonus.

        Before: 10 shares * Rs.3,500 = Rs.35,000
        After:  20 shares * Rs.1,750 = Rs.35,000
        """
        portfolio = Portfolio(initial_capital=200_000.0)
        portfolio.buy("TCS", 10, 3500.0, date(2018, 6, 1))

        pre_cost_basis = 10 * 3500.0
        pre_cash = portfolio.cash

        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )
        factor = compute_adjustment_factor(ca)
        portfolio.apply_corporate_action("TCS", factor.share_factor)

        pos = portfolio.positions["TCS"]
        assert pos.quantity == 20, f"Expected 20 shares, got {pos.quantity}"
        assert pos.avg_cost == pytest.approx(1750.0), (
            f"Expected avg_cost 1750.0, got {pos.avg_cost:.2f}"
        )

        post_cost_basis = pos.quantity * pos.avg_cost
        assert post_cost_basis == pytest.approx(pre_cost_basis)
        assert portfolio.cash == pytest.approx(pre_cash)

    # ── Layer 5: NAV continuity ──

    def test_layer5_nav_continuity(self):
        """Run full backtest through TCS bonus; verify no phantom jump."""
        raw_df = load_fixture("tcs_bonus_2018_raw.csv")

        ca = CorporateAction(
            security_symbol="TCS",
            action_type="BONUS",
            ex_date=date(2018, 6, 14),
            ratio_new=1,
            ratio_existing=1,
        )

        result = run_backtest(
            data_source=DataFrameSource(raw_df),
            signal_generator=BuyAndHold(),
            symbol="TCS",
            trade_qty=10,
            initial_capital=200_000.0,
            cost_model=NoCostModel(),
            corporate_actions=[ca],
        )

        curve = result.equity_curve
        for i in range(1, len(curve)):
            prev_eq = curve[i - 1]["equity"]
            curr_eq = curve[i]["equity"]
            if prev_eq > 0:
                daily_pct = (curr_eq - prev_eq) / prev_eq
                assert abs(daily_pct) < 0.15, (
                    f"Excessive change on {curve[i]['trading_date']}: "
                    f"{daily_pct:.2%}"
                )


# ════════════════════════════════════════════════════════════════
# EVENT C: INFY Final Dividend Rs.17.50/share
# NSE ex-date: 2023-06-02
# ════════════════════════════════════════════════════════════════


class TestRealDividendINFY:
    """
    Validate dividend handling against INFY's Rs.17.50 final dividend.

    NSE source: INFY (Infosys Limited)
    Event: Final dividend Rs.17.50 per share
    Ex-date: 2023-06-02

    Current design: Dividends produce a warning and pass through
    with factor 1.0 (no price adjustment, no cash injection).

    This is a KNOWN LIMITATION documented in the design.
    The test verifies the system handles it correctly (warns, doesn't corrupt).
    """

    def test_layer1_dividend_action_parsing(self):
        """Verify CorporateAction for INFY dividend."""
        ca = CorporateAction(
            security_symbol="INFY",
            action_type="DIVIDEND",
            ex_date=date(2023, 6, 2),
            dividend_per_share=17.50,
        )
        assert ca.action_type == "DIVIDEND"
        assert ca.dividend_per_share == 17.50

    def test_layer2_dividend_factor_identity(self):
        """Dividend should produce identity factors (1.0)."""
        ca = CorporateAction(
            security_symbol="INFY",
            action_type="DIVIDEND",
            ex_date=date(2023, 6, 2),
            dividend_per_share=17.50,
        )
        factor = compute_adjustment_factor(ca)
        assert factor.price_factor == 1.0
        assert factor.volume_factor == 1.0
        assert factor.share_factor == 1.0

    def test_layer3_dividend_prices_unchanged(self):
        """
        Dividend should NOT adjust prices.

        Shares remain unchanged, cash should increase by
        dividend * eligible shares. Current implementation
        does NOT inject cash (known limitation).
        """
        # Use the yfinance-adjusted fixture (Close is not split-affected
        # for INFY in this period, so it's effectively raw)
        df = load_fixture("infy_dividend_2023.csv")

        ca = CorporateAction(
            security_symbol="INFY",
            action_type="DIVIDEND",
            ex_date=date(2023, 6, 2),
            dividend_per_share=17.50,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(df, [ca])

        # All prices should be unchanged (factor = 1.0 throughout)
        for i in range(len(df)):
            orig_close = df.iloc[i]["close"]
            adj_close = adjusted.iloc[i]["close"]
            assert adj_close == pytest.approx(orig_close, rel=1e-6), (
                f"Price changed on {df.iloc[i]['trading_date']}: "
                f"original={orig_close}, adjusted={adj_close}"
            )

    def test_layer4_portfolio_shares_unchanged(self):
        """
        Through a dividend, share count must NOT change.
        avg_cost must NOT change.

        KNOWN LIMITATION: Cash injection for dividends is not yet
        implemented. The system warns but does not add dividend cash.
        """
        portfolio = Portfolio(initial_capital=200_000.0)
        portfolio.buy("INFY", 100, 1320.0, date(2023, 5, 29))

        pre_shares = portfolio.positions["INFY"].quantity
        pre_avg_cost = portfolio.positions["INFY"].avg_cost
        pre_cash = portfolio.cash

        # apply_corporate_action with share_factor=1.0 should be a no-op
        ca = CorporateAction(
            security_symbol="INFY",
            action_type="DIVIDEND",
            ex_date=date(2023, 6, 2),
            dividend_per_share=17.50,
        )
        factor = compute_adjustment_factor(ca)

        # share_factor = 1.0, so apply_corporate_action returns immediately
        portfolio.apply_corporate_action("INFY", factor.share_factor)

        pos = portfolio.positions["INFY"]
        assert pos.quantity == pre_shares, "Shares changed through dividend"
        assert pos.avg_cost == pytest.approx(pre_avg_cost), (
            "avg_cost changed through dividend"
        )
        assert portfolio.cash == pytest.approx(pre_cash), (
            "Cash changed unexpectedly (dividend cash injection not implemented)"
        )

    def test_dividend_cash_injection_not_implemented(self):
        """
        Document that dividend cash injection is NOT yet implemented.

        Expected behavior when implemented:
            cash += dividend_per_share * eligible_shares
            = 17.50 * 100 = Rs.1,750

        Current behavior: no cash change (known limitation).
        """
        # This test documents the limitation rather than asserting failure
        portfolio = Portfolio(initial_capital=200_000.0)
        portfolio.buy("INFY", 100, 1320.0, date(2023, 5, 29))

        pre_cash = portfolio.cash
        expected_dividend_cash = 17.50 * 100  # Rs.1,750

        # Current implementation: no mechanism to inject dividend cash
        # When this is implemented, this test should be updated to verify
        # that cash increases by expected_dividend_cash
        assert portfolio.cash == pytest.approx(pre_cash), (
            "Cash should remain unchanged until dividend injection "
            "is implemented"
        )


# ════════════════════════════════════════════════════════════════
# EX-DATE SEMANTICS (all events)
# ════════════════════════════════════════════════════════════════


class TestExDateSemantics:
    """
    Verify ex-date boundary behavior for split and bonus events.

    For every event:
      Previous trading day -> adjusted historical scale
      Ex-date             -> post-action scale
      Following day       -> same post-action scale

    There must be no artificial price jump in the adjusted series.
    """

    def test_irctc_split_three_day_window(self):
        """IRCTC: Oct 28 (pre) -> Oct 29 (ex) -> Nov 1 (post)."""
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC", action_type="SPLIT",
            ex_date=date(2021, 10, 29), ratio_new=5, ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        pre = adjusted[adjusted["trading_date"] == date(2021, 10, 28)].iloc[0]
        ex = adjusted[adjusted["trading_date"] == date(2021, 10, 29)].iloc[0]
        post = adjusted[adjusted["trading_date"] == date(2021, 11, 1)].iloc[0]

        # All three should be on the same scale (~800-920 range)
        assert 600 < pre["close"] < 1200, f"Pre-split adj close out of range: {pre['close']}"
        assert 600 < ex["close"] < 1200, f"Ex-date close out of range: {ex['close']}"
        assert 600 < post["close"] < 1200, f"Post-split close out of range: {post['close']}"

        # No jump > 20% between consecutive days
        assert abs(ex["close"] - pre["close"]) / pre["close"] < 0.20
        assert abs(post["close"] - ex["close"]) / ex["close"] < 0.20

    def test_tcs_bonus_three_day_window(self):
        """TCS: Jun 13 (pre) -> Jun 14 (ex) -> Jun 15 (post)."""
        raw_df = load_fixture("tcs_bonus_2018_raw.csv")

        ca = CorporateAction(
            security_symbol="TCS", action_type="BONUS",
            ex_date=date(2018, 6, 14), ratio_new=1, ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        pre = adjusted[adjusted["trading_date"] == date(2018, 6, 13)].iloc[0]
        ex = adjusted[adjusted["trading_date"] == date(2018, 6, 14)].iloc[0]
        post = adjusted[adjusted["trading_date"] == date(2018, 6, 15)].iloc[0]

        # All three should be on the same scale (~1700-1900 range)
        assert 1500 < pre["close"] < 2200
        assert 1500 < ex["close"] < 2200
        assert 1500 < post["close"] < 2200

        assert abs(ex["close"] - pre["close"]) / pre["close"] < 0.10
        assert abs(post["close"] - ex["close"]) / ex["close"] < 0.10


# ════════════════════════════════════════════════════════════════
# DOUBLE-ADJUSTMENT SAFEGUARD
# ════════════════════════════════════════════════════════════════


class TestDoubleAdjustmentSafeguard:
    """
    Verify that applying adjustment to already-adjusted data
    does not silently produce incorrect results.

    The design identifies this as a critical risk when using
    yfinance data (which may be pre-adjusted).
    """

    def test_raw_adjustment_produces_correct_result(self):
        """RAW -> adjust -> correct."""
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC", action_type="SPLIT",
            ex_date=date(2021, 10, 29), ratio_new=5, ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        # Pre-split raw close: ~4567.50, adjusted: ~913.50
        pre = adjusted[adjusted["trading_date"] == date(2021, 10, 28)]
        assert pre.iloc[0]["close"] == pytest.approx(913.50, rel=0.01)

    def test_double_adjustment_produces_wrong_result(self):
        """
        ALREADY ADJUSTED -> attempt adjustment -> WRONG.

        This test demonstrates that adjusting already-adjusted data
        (like yfinance's Close column) produces incorrect prices.
        The user must verify their data source before applying adjustment.
        """
        # Use the yfinance-adjusted fixture (already split-adjusted)
        already_adj = load_fixture("irctc_split_2021.csv")

        ca = CorporateAction(
            security_symbol="IRCTC", action_type="SPLIT",
            ex_date=date(2021, 10, 29), ratio_new=5, ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        double_adjusted = adjuster.adjust(already_adj, [ca])

        # Oct 28 already-adjusted close: ~913.50
        # Double-adjusted: 913.50 * 0.2 = ~182.70 (WRONG!)
        pre = double_adjusted[double_adjusted["trading_date"] == date(2021, 10, 28)]
        double_adj_close = pre.iloc[0]["close"]

        # This is wrong — it should be ~913, not ~183
        # The test DOCUMENTS the danger of double-adjustment
        assert double_adj_close < 200, (
            "Double-adjusted price should be ~183 (demonstrably wrong)"
        )
        assert double_adj_close != pytest.approx(913.50, rel=0.1), (
            "Double-adjusted should NOT match the correct value"
        )

    def test_adjustment_factor_column_for_auditability(self):
        """
        The adjustment_factor column allows the user to detect
        whether data has been adjusted and by how much.
        """
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC", action_type="SPLIT",
            ex_date=date(2021, 10, 29), ratio_new=5, ratio_existing=1,
        )

        adjuster = PriceAdjuster()
        adjusted = adjuster.adjust(raw_df, [ca])

        assert "adjustment_factor" in adjusted.columns
        # Pre-split rows: factor = 0.2
        pre = adjusted[adjusted["trading_date"] < date(2021, 10, 29)]
        for _, row in pre.iterrows():
            assert row["adjustment_factor"] == pytest.approx(0.2), (
                f"Date {row['trading_date']}: factor {row['adjustment_factor']}"
            )

        # Post-split rows: factor = 1.0
        post = adjusted[adjusted["trading_date"] >= date(2021, 10, 29)]
        for _, row in post.iterrows():
            assert row["adjustment_factor"] == pytest.approx(1.0), (
                f"Date {row['trading_date']}: factor {row['adjustment_factor']}"
            )


# ════════════════════════════════════════════════════════════════
# UNSUPPORTED CORPORATE ACTIONS
# ════════════════════════════════════════════════════════════════


class TestUnsupportedActionsReal:
    """
    Verify unsupported actions fail explicitly, not silently.

    Rights, mergers, demergers, and symbol changes are identified
    in the design as more complex cases not yet implemented.
    """

    def test_rights_issue_fails_explicitly(self):
        """Rights issue must raise UnsupportedCorporateActionError."""
        ca = CorporateAction(
            security_symbol="RELIANCE",
            action_type="RIGHTS_ISSUE",
            ex_date=date(2020, 5, 14),
            ratio_new=1,
            ratio_existing=15,
        )
        with pytest.raises(UnsupportedCorporateActionError):
            compute_adjustment_factor(ca)

    def test_merger_fails_explicitly(self):
        """Merger must raise UnsupportedCorporateActionError."""
        ca = CorporateAction(
            security_symbol="HDFCBANK",
            action_type="MERGER",
            ex_date=date(2023, 7, 13),
        )
        with pytest.raises(UnsupportedCorporateActionError):
            compute_adjustment_factor(ca)

    def test_demerger_fails_explicitly(self):
        """Demerger must raise UnsupportedCorporateActionError."""
        ca = CorporateAction(
            security_symbol="ITC",
            action_type="DEMERGER",
            ex_date=date(2024, 1, 1),
        )
        with pytest.raises(UnsupportedCorporateActionError):
            compute_adjustment_factor(ca)

    def test_symbol_change_is_identity(self):
        """
        Symbol change is supported with identity factors (1.0).
        It does not alter prices, volumes, or shares.
        """
        ca = CorporateAction(
            security_symbol="VEDL",
            action_type="SYMBOL_CHANGE",
            ex_date=date(2023, 1, 1),
            old_symbol="VEDL",
            new_symbol="VEDANTA",
        )
        factor = compute_adjustment_factor(ca)
        assert factor.price_factor == 1.0
        assert factor.volume_factor == 1.0
        assert factor.share_factor == 1.0


# ════════════════════════════════════════════════════════════════
# TRADE-PRICE CONSISTENCY AUDIT
# ════════════════════════════════════════════════════════════════


class TestTradePriceConsistency:
    """
    Verify that signal, execution, trade log, and portfolio
    accounting prices are all on the same (adjusted) scale.

    The design raised this as an open question. This test
    documents what the current implementation actually does.
    """

    def test_trade_log_uses_adjusted_prices(self):
        """
        In the current implementation, the entire pipeline operates
        on adjusted prices. Therefore:
          - signal prices: adjusted
          - execution prices: adjusted (fills at adjusted open)
          - trade log prices: adjusted
          - portfolio avg_cost: adjusted
          - equity mark-to-market: adjusted close

        This test verifies consistency.
        """
        raw_df = load_fixture("tcs_bonus_2018_raw.csv")

        ca = CorporateAction(
            security_symbol="TCS", action_type="BONUS",
            ex_date=date(2018, 6, 14), ratio_new=1, ratio_existing=1,
        )

        result = run_backtest(
            data_source=DataFrameSource(raw_df),
            signal_generator=BuyAndHold(),
            symbol="TCS",
            trade_qty=10,
            initial_capital=200_000.0,
            cost_model=NoCostModel(),
            corporate_actions=[ca],
        )

        # The BUY trade should be at an adjusted price (~1750-1850)
        buy_trades = [t for t in result.trade_log if t.side == "BUY"]
        assert len(buy_trades) >= 1, "No BUY trade found"

        buy_price = buy_trades[0].price
        # Adjusted TCS price should be in the ~1700-1900 range
        # NOT in the ~3400-3800 raw range
        assert 1500 < buy_price < 2200, (
            f"BUY price {buy_price:.2f} appears to be on raw scale "
            f"(expected adjusted ~1700-1900)"
        )

    def test_price_scale_consistency_across_layers(self):
        """
        All prices in a backtest result should be on the same scale.

        Signal price (adjusted) == execution price (adjusted)
        == trade log price == portfolio accounting price.
        """
        raw_df = load_fixture("irctc_split_2021_raw.csv")

        ca = CorporateAction(
            security_symbol="IRCTC", action_type="SPLIT",
            ex_date=date(2021, 10, 29), ratio_new=5, ratio_existing=1,
        )

        result = run_backtest(
            data_source=DataFrameSource(raw_df),
            signal_generator=BuyAndHold(),
            symbol="IRCTC",
            trade_qty=10,
            initial_capital=500_000.0,
            cost_model=NoCostModel(),
            corporate_actions=[ca],
        )

        # Check BUY trade price
        buy_trades = [t for t in result.trade_log if t.side == "BUY"]
        if buy_trades:
            buy_price = buy_trades[0].price
            # Must be adjusted scale (700-1000), not raw (3500-5000)
            assert buy_price < 1500, (
                f"Trade log price {buy_price:.2f} is not on adjusted scale"
            )

        # Check equity curve values are reasonable
        for point in result.equity_curve:
            eq = point["equity"]
            # With 500k capital and 10 shares at ~900, equity should be
            # around 491,000-509,000 (not 455,000 from raw or 545,000)
            assert 480_000 < eq < 520_000, (
                f"Equity {eq:.2f} on {point['trading_date']} "
                f"seems inconsistent"
            )


# ════════════════════════════════════════════════════════════════
# DATA SOURCING FINDINGS (documented as tests)
# ════════════════════════════════════════════════════════════════


class TestDataSourcingFindings:
    """
    Document critical findings about yfinance data sourcing.

    These tests encode knowledge about yfinance behavior that
    affects how we must handle data in the backtester.
    """

    def test_yfinance_close_is_split_adjusted(self):
        """
        FINDING: yfinance auto_adjust=False Close is ALREADY
        adjusted for splits and bonuses.

        IRCTC Oct 28 (pre-split): yfinance Close = ~913
        Real NSE Close: ~4,567 (= 913 * 5)

        This means our data pipeline cannot use yfinance Close
        as "raw" data without reversing the split adjustment.
        """
        yf_adj = load_fixture("irctc_split_2021.csv")
        raw = load_fixture("irctc_split_2021_raw.csv")

        oct28_yf = yf_adj[yf_adj["trading_date"] == date(2021, 10, 28)]
        oct28_raw = raw[raw["trading_date"] == date(2021, 10, 28)]

        yf_close = oct28_yf.iloc[0]["close"]
        raw_close = oct28_raw.iloc[0]["close"]

        # yfinance Close is already adjusted (factor of 5 difference)
        assert raw_close == pytest.approx(yf_close * 5, rel=0.01), (
            f"Raw {raw_close:.2f} should be ~5x yfinance {yf_close:.2f}"
        )

    def test_yfinance_bonus_exdate_discrepancy(self):
        """
        FINDING: yfinance may report a different ex-date than NSE.

        TCS 1:1 bonus:
          yfinance split event: 2018-05-31
          NSE actual ex-date:   2018-06-14

        This 14-day discrepancy means we cannot rely on yfinance
        for authoritative ex-dates. NSE announcements must be
        used as the source of truth.
        """
        # This is a documentation test — it encodes the finding
        yf_split_date = date(2018, 5, 31)
        nse_exdate = date(2018, 6, 14)
        discrepancy_days = (nse_exdate - yf_split_date).days

        assert discrepancy_days == 14, (
            f"Expected 14-day discrepancy, got {discrepancy_days}"
        )
        # The authoritative source for ex-dates is NSE, not yfinance
