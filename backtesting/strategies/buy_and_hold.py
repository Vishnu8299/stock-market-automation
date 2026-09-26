"""
Buy-and-Hold strategy — the benchmark baseline.

Rules:
  - BUY on the very first bar.
  - HOLD forever after that.
  - Never SELL.

This is the simplest possible benchmark: it answers "what if we just
bought on day 1 and did nothing?"  Any active strategy that can't beat
buy-and-hold isn't worth running.
"""

import pandas as pd

from backtesting.core.signal import Signal, SignalInterface


class BuyAndHold(SignalInterface):
    """
    Buy once on the first bar, hold forever.

    The engine's execution simulator will buy at the next bar's open
    (standard next-bar fill), so the effective entry is bar-2's open price.
    """

    @property
    def name(self) -> str:
        return "Buy_and_Hold"

    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Add `signal` column: BUY on the first bar, HOLD on all others.
        """
        df = data.copy()
        df["signal"] = Signal.HOLD
        if len(df) > 0:
            df.iloc[0, df.columns.get_loc("signal")] = Signal.BUY
        return df
