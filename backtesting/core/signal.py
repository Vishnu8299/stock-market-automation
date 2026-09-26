"""
Signal interface — abstract signal generator for trading strategies.

A signal generator receives the full OHLCV DataFrame and returns it
with an additional `signal` column containing Signal enum values.
The engine decides which signals to act on based on the simulation
date range; the generator itself is pure computation, no side effects.
"""

from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class Signal(Enum):
    """Trading signal emitted by a strategy."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalInterface(ABC):
    """
    Abstract base for signal generators.

    Subclasses must implement `generate()`, which takes an OHLCV
    DataFrame and returns a copy with an added `signal` column.
    """

    @abstractmethod
    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Compute signals for every bar in `data`.

        Parameters
        ----------
        data : DataFrame
            Must contain at least [trading_date, open, high, low, close, volume].

        Returns
        -------
        DataFrame
            Same rows as `data` plus a `signal` column of Signal values.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name for reports."""
