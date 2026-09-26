"""
50/200 SMA Crossover strategy — the M0.2 baseline.

Rules:
  - Compute SMA(50) and SMA(200) on the close price.
  - BUY  when SMA-50 crosses ABOVE SMA-200 (golden cross).
  - SELL when SMA-50 crosses BELOW SMA-200 (death cross).
  - HOLD on all other bars, and during the 200-bar warm-up period.

Limitations (M0.2):
  - Operates on raw (unadjusted) prices — corporate actions are not
    accounted for.  This can cause spurious signals around split/bonus
    ex-dates.  Acceptable for the baseline; adjustment will be added
    when the corporate-actions pipeline is ready.
  - Single-symbol only (no universe rotation).
"""

import pandas as pd

from backtesting.core.signal import Signal, SignalInterface


class SmaCrossover(SignalInterface):
    """
    SMA crossover signal generator.

    Parameters
    ----------
    fast_period : int
        Lookback for the fast moving average (default 50).
    slow_period : int
        Lookback for the slow moving average (default 200).
    """

    def __init__(self, fast_period: int = 50, slow_period: int = 200):
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period

    @property
    def name(self) -> str:
        return f"SMA_{self.fast_period}_{self.slow_period}"

    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Add `signal` column with BUY/SELL/HOLD based on SMA crossover.

        The crossover is detected by comparing the relative position of
        fast vs. slow SMA on consecutive bars:
          - If fast was <= slow yesterday AND fast > slow today → BUY
          - If fast was >= slow yesterday AND fast < slow today → SELL
          - Otherwise → HOLD
        """
        df = data.copy()

        df["sma_fast"] = df["close"].rolling(window=self.fast_period, min_periods=self.fast_period).mean()
        df["sma_slow"] = df["close"].rolling(window=self.slow_period, min_periods=self.slow_period).mean()

        # Default to HOLD
        df["signal"] = Signal.HOLD

        # Need at least slow_period bars before any signal is possible,
        # plus one bar for the crossover comparison.
        valid = df["sma_fast"].notna() & df["sma_slow"].notna()

        # Crossover detection: compare today vs yesterday
        fast = df["sma_fast"]
        slow = df["sma_slow"]
        fast_prev = fast.shift(1)
        slow_prev = slow.shift(1)

        golden_cross = valid & (fast_prev <= slow_prev) & (fast > slow)
        death_cross = valid & (fast_prev >= slow_prev) & (fast < slow)

        df.loc[golden_cross, "signal"] = Signal.BUY
        df.loc[death_cross, "signal"] = Signal.SELL

        # Drop intermediate columns — caller doesn't need them
        df = df.drop(columns=["sma_fast", "sma_slow"])

        return df
