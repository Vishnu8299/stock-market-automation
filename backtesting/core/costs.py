"""
Cost model — transaction cost abstractions.

CostModel (ABC)         — interface for computing per-trade costs
NoCostModel             — zero costs, useful for pure-signal evaluation
PercentageCostModel     — flat percentage of trade value

Default brokerage is 0.1% — a rough approximation for Indian discount
brokers (Zerodha, Groww, etc.).  Does not yet model STT, stamp duty,
SEBI turnover fee, or GST separately; those can be added as a more
detailed IndianEquityCostModel in a future milestone if needed.
"""

from abc import ABC, abstractmethod


class CostModel(ABC):
    """Abstract base for transaction cost calculation."""

    @abstractmethod
    def calculate(self, price: float, quantity: int, side: str) -> float:
        """
        Compute the total cost for a single trade.

        Parameters
        ----------
        price : float
            Fill price per share.
        quantity : int
            Number of shares.
        side : str
            'BUY' or 'SELL'.

        Returns
        -------
        float
            Total cost (always non-negative).
        """


class NoCostModel(CostModel):
    """Zero transaction costs — useful for isolating signal quality."""

    def calculate(self, price: float, quantity: int, side: str) -> float:
        return 0.0


class PercentageCostModel(CostModel):
    """
    Flat percentage of trade notional value.

    Parameters
    ----------
    rate : float
        Cost as a fraction of trade value, e.g. 0.001 = 0.1%.
    """

    def __init__(self, rate: float = 0.001):
        if rate < 0:
            raise ValueError(f"Cost rate must be non-negative, got {rate}")
        self.rate = rate

    def calculate(self, price: float, quantity: int, side: str) -> float:
        return abs(price * quantity) * self.rate
