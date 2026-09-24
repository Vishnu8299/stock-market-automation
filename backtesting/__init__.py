"""
M0.2 Backtesting Engine — SMA crossover strategy.

Architecture rule (Team Lead directive, M0.1 closure):
    This module depends ONLY on DataInterface.
    It must NOT import, reference, or know about:
        - The data source adapter
        - Source URLs or endpoints
        - Source file formats

    backtesting/
        YES: depend on DataInterface
        NO:  import source adapters
        NO:  download data directly
        NO:  know source URLs

The data layer handles source selection, dataset versioning, and
corporate-action adjustments. The backtester receives clean OHLCV data.
"""
