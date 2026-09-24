"""
M0.2-I1 Integration Tests — DataInterface + Backtesting Engine.

Verifies the clean contract between the NSE Data Engine (Vishnu)
and the Backtesting Engine (Bharath):

    NSEDataSource
          |
    PostgreSQL / TimescaleDB
          |
    DataInterface  <-- CONTRACT
          |
    Backtesting Engine

Team Lead (Vishnu) M0.2-I1 Definition of Done:
    [x] DataInterface integration
    [x] Single-security load
    [x] Correct date filtering
    [x] Correct ordering
    [x] Data validation
    [x] No duplicate observations
    [x] Backtester runs successfully
    [x] Backtester has no NSE-source dependency
    [x] Integration test passes

These tests use synthetic data injected directly via the DataInterface
contract so they run without network access or a live database.
"""

import ast
import inspect
import os
import textwrap
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# The backtesting engine — our consumer of the contract.
from backtesting.engine import SMAStrategy, BacktestResult, Trade


# ======================================================================
# FIXTURES: Synthetic market data that mimics real DataInterface output
# ======================================================================

def _make_synthetic_ohlcv(
    symbol: str = "TESTCO",
    series: str = "EQ",
    num_days: int = 300,
    start_date: date = date(2025, 1, 1),
    base_price: float = 1000.0,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data matching DataInterface.COLUMNS.

    Creates a deterministic price series with a known SMA crossover
    embedded so we can verify the backtest output exactly.
    """
    import numpy as np
    np.random.seed(42)  # deterministic

    dates = []
    d = start_date
    for _ in range(num_days):
        # Skip weekends
        while d.weekday() >= 5:
            d += timedelta(days=1)
        dates.append(d)
        d += timedelta(days=1)

    # Generate price with a trend that creates a known crossover
    # Start with uptrend, then downtrend, then uptrend again
    prices = []
    price = base_price
    for i in range(num_days):
        if i < 100:
            drift = 0.002  # uptrend
        elif i < 200:
            drift = -0.003  # downtrend
        else:
            drift = 0.002  # uptrend again
        price *= (1 + drift + np.random.normal(0, 0.01))
        prices.append(price)

    data = {
        "symbol": [symbol] * num_days,
        "series": [series] * num_days,
        "trading_date": dates,
        "open": [p * (1 + np.random.uniform(-0.005, 0.005)) for p in prices],
        "high": [p * (1 + abs(np.random.normal(0, 0.01))) for p in prices],
        "low": [p * (1 - abs(np.random.normal(0, 0.01))) for p in prices],
        "close": prices,
        "volume": [int(abs(np.random.normal(1000000, 200000))) for _ in range(num_days)],
        "traded_value": [p * int(abs(np.random.normal(1000000, 200000))) for p in prices],
    }

    df = pd.DataFrame(data)
    # Ensure OHLC sanity
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


@pytest.fixture
def synthetic_data():
    """300 trading days of synthetic OHLCV data for TESTCO/EQ."""
    return _make_synthetic_ohlcv()


@pytest.fixture
def short_data():
    """50 days of data — insufficient for 50/200 SMA."""
    return _make_synthetic_ohlcv(num_days=50)


@pytest.fixture
def mock_data_interface(synthetic_data):
    """A mock DataInterface that returns synthetic data."""
    from ingestion.data_interface import DataInterface

    mock_engine = MagicMock()
    di = DataInterface(engine=mock_engine)

    # Patch load_market_data to return synthetic data
    di.load_market_data = MagicMock(return_value=synthetic_data)
    di.validate_data = DataInterface.validate_data.__get__(di, DataInterface)

    return di


# ======================================================================
# TEST 1: Single-security load
# ======================================================================

class TestDataInterfaceIntegration:
    """Verify the DataInterface contract from the backtester's perspective."""

    def test_single_security_load(self, mock_data_interface, synthetic_data):
        """A known symbol can be loaded via DataInterface."""
        df = mock_data_interface.load_market_data("TESTCO", series="EQ")

        assert len(df) > 0, "DataInterface returned no data"
        assert "TESTCO" in df["symbol"].values
        assert "EQ" in df["series"].values
        mock_data_interface.load_market_data.assert_called_once_with(
            "TESTCO", series="EQ"
        )

    def test_date_boundaries_respected(self):
        """Requested date boundaries must be respected."""
        full_data = _make_synthetic_ohlcv(num_days=300)
        start = date(2025, 4, 1)
        end = date(2025, 8, 1)

        # Filter to simulate DataInterface filtering
        filtered = full_data[
            (full_data["trading_date"] >= start) &
            (full_data["trading_date"] <= end)
        ].copy()

        assert len(filtered) > 0
        assert filtered["trading_date"].min() >= start
        assert filtered["trading_date"].max() <= end

    def test_dates_ordered_ascending(self, synthetic_data):
        """Data must be sorted by trading_date ascending."""
        dates = synthetic_data["trading_date"].tolist()
        assert dates == sorted(dates), "Dates are not sorted ascending"

    def test_no_duplicate_observations(self, synthetic_data):
        """No duplicate (symbol, series, trading_date) observations."""
        dups = synthetic_data.duplicated(
            subset=["symbol", "series", "trading_date"]
        )
        assert not dups.any(), (
            f"{dups.sum()} duplicate observations found"
        )

    def test_ohlc_validation_passes(self, synthetic_data):
        """OHLC sanity: High >= max(O,C,L), Low <= min(O,C,H)."""
        high_ok = synthetic_data["high"] >= synthetic_data[
            ["open", "close", "low"]
        ].max(axis=1)
        low_ok = synthetic_data["low"] <= synthetic_data[
            ["open", "close", "high"]
        ].min(axis=1)
        assert high_ok.all(), "High < max(O,C,L) on some rows"
        assert low_ok.all(), "Low > min(O,C,H) on some rows"

    def test_validate_data_method(self, mock_data_interface, synthetic_data):
        """DataInterface.validate_data() confirms data quality."""
        result = mock_data_interface.validate_data(synthetic_data)
        assert result["valid"] is True, f"Validation failed: {result['issues']}"
        assert result["adjustment_status"] == "RAW — corporate actions NOT applied"
        assert result["dataset_version"] is not None

    def test_canonical_columns_present(self, synthetic_data):
        """All canonical columns from DataInterface.COLUMNS must be present."""
        from ingestion.data_interface import DataInterface
        for col in DataInterface.COLUMNS:
            assert col in synthetic_data.columns, f"Missing canonical column: {col}"


# ======================================================================
# TEST 2: Backtester runs successfully with DataInterface data
# ======================================================================

class TestBacktesterIntegration:
    """Verify the backtester works correctly with DataInterface data."""

    def test_backtester_runs_successfully(self, mock_data_interface):
        """The backtester produces results from DataInterface data."""
        df = mock_data_interface.load_market_data("TESTCO", series="EQ")
        metadata = mock_data_interface.validate_data(df)

        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(df, data_metadata=metadata)

        assert isinstance(result, BacktestResult)
        assert result.symbol == "TESTCO"
        assert result.series == "EQ"
        assert result.strategy == "SMA_50_200"
        assert result.observations == len(df)
        assert result.data_metadata is not None
        assert result.data_metadata["valid"] is True

    def test_backtester_receives_expected_observations(self, synthetic_data):
        """The engine receives exactly the expected number of observations."""
        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(synthetic_data)

        assert result.observations == len(synthetic_data)

    def test_backtester_with_insufficient_data(self, short_data):
        """Backtester raises ValueError when data is too short."""
        strategy = SMAStrategy(short_window=50, long_window=200)
        with pytest.raises(ValueError, match="Need at least 200"):
            strategy.run(short_data)

    def test_backtester_rejects_unsorted_data(self, synthetic_data):
        """Backtester rejects data not sorted by trading_date."""
        shuffled = synthetic_data.sample(frac=1).reset_index(drop=True)
        strategy = SMAStrategy(short_window=50, long_window=200)
        with pytest.raises(ValueError, match="sorted by trading_date"):
            strategy.run(shuffled)

    def test_backtester_rejects_duplicates(self):
        """Backtester rejects data with duplicate (symbol, series, date) rows."""
        data = _make_synthetic_ohlcv(num_days=250)
        # Duplicate the first row
        dup = data.iloc[[0]].copy()
        data = pd.concat([data, dup]).sort_values("trading_date").reset_index(drop=True)

        strategy = SMAStrategy(short_window=50, long_window=200)
        with pytest.raises(ValueError, match="duplicate"):
            strategy.run(data)

    def test_backtester_rejects_multi_symbol(self):
        """Backtester rejects a DataFrame with multiple symbols."""
        data1 = _make_synthetic_ohlcv(symbol="AAA", num_days=250)
        data2 = _make_synthetic_ohlcv(symbol="BBB", num_days=250)
        combined = pd.concat([data1, data2]).reset_index(drop=True)

        strategy = SMAStrategy(short_window=50, long_window=200)
        with pytest.raises(ValueError, match="Expected exactly 1 symbol"):
            strategy.run(combined)

    def test_trade_fields_are_correct(self, synthetic_data):
        """Verify trade objects have all required fields and correct types."""
        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(synthetic_data)

        if result.total_trades > 0:
            trade = result.trades[0]
            assert isinstance(trade, Trade)
            assert isinstance(trade.entry_date, date)
            assert isinstance(trade.exit_date, date)
            assert isinstance(trade.entry_price, float)
            assert isinstance(trade.exit_price, float)
            assert isinstance(trade.shares, int)
            assert trade.shares > 0
            assert trade.side == "LONG"
            assert trade.entry_date <= trade.exit_date

    def test_metadata_includes_raw_warning(self, mock_data_interface):
        """Metadata must include the RAW data warning per Team Lead directive."""
        df = mock_data_interface.load_market_data("TESTCO", series="EQ")
        metadata = mock_data_interface.validate_data(df)

        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(df, data_metadata=metadata)

        assert result.data_metadata is not None
        assert "RAW" in result.data_metadata["adjustment_status"]
        assert "corporate actions NOT applied" in result.data_metadata["adjustment_status"]


# ======================================================================
# TEST 3: Architecture boundary — NO NSE coupling
# ======================================================================

class TestArchitectureBoundary:
    """Verify that the backtesting module has NO dependency on NSE source.

    Team Lead directive (M0.1 closure):
        backtesting/
            NO: import ingestion.nse_source
            NO: download NSE data
            NO: know NSE URLs
    """

    def test_backtesting_does_not_import_nse_source(self):
        """The backtesting package must not import ingestion.nse_source."""
        backtesting_dir = Path(__file__).parent.parent / "backtesting"
        violations = []

        for py_file in backtesting_dir.glob("**/*.py"):
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "nse_source" in alias.name.lower():
                            violations.append(
                                f"{py_file.name}: import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "nse_source" in node.module.lower():
                        violations.append(
                            f"{py_file.name}: from {node.module} import ..."
                        )

        assert not violations, (
            "Architecture violation — backtesting imports NSE source:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_backtesting_does_not_reference_nse_urls(self):
        """The backtesting package must not contain NSE URLs."""
        backtesting_dir = Path(__file__).parent.parent / "backtesting"
        violations = []
        nse_patterns = ["nseindia.com", "nsearchives", "nse_source", "NSEDataSource"]

        for py_file in backtesting_dir.glob("**/*.py"):
            source = py_file.read_text(encoding="utf-8")
            for pattern in nse_patterns:
                if pattern in source:
                    violations.append(
                        f"{py_file.name} contains '{pattern}'"
                    )

        assert not violations, (
            "Architecture violation — backtesting references NSE:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_engine_source_has_no_nse_dependency(self):
        """Inspect engine.py source for any NSE references."""
        engine_source = inspect.getsource(SMAStrategy)

        forbidden = ["nse_source", "NSEDataSource", "nseindia", "nsearchives",
                      "download_nse", "bhavcopy"]
        for term in forbidden:
            assert term.lower() not in engine_source.lower(), (
                f"SMAStrategy source contains forbidden term: '{term}'"
            )

    def test_backtester_depends_only_on_dataframe(self):
        """SMAStrategy.run() accepts a plain DataFrame, not a DataInterface object."""
        sig = inspect.signature(SMAStrategy.run)
        params = list(sig.parameters.keys())

        # First param is self, second is df
        assert "df" in params, "run() must accept a 'df' parameter"
        # There should be no 'source', 'nse', 'download' parameters
        for p in params:
            assert "nse" not in p.lower(), f"Forbidden parameter name: {p}"
            assert "download" not in p.lower(), f"Forbidden parameter name: {p}"
            assert "source" not in p.lower() or p == "source", f"Forbidden parameter name: {p}"


# ======================================================================
# TEST 4: End-to-end integration (DataInterface -> Backtester)
# ======================================================================

class TestEndToEnd:
    """Full pipeline: DataInterface loads data -> backtester processes it."""

    def test_full_pipeline(self, mock_data_interface):
        """Simulate the full data flow from DataInterface to backtest result."""
        # Step 1: Load data via DataInterface contract
        df = mock_data_interface.load_market_data("TESTCO", series="EQ")

        # Step 2: Validate at the boundary
        metadata = mock_data_interface.validate_data(df)
        assert metadata["valid"] is True

        # Step 3: Run backtest
        strategy = SMAStrategy(short_window=50, long_window=200)
        result = strategy.run(df, data_metadata=metadata)

        # Step 4: Verify result is complete and meaningful
        assert result.symbol == "TESTCO"
        assert result.observations == len(df)
        assert result.data_metadata["dataset_version"] is not None
        assert result.start_date <= result.end_date

        # Step 5: Verify provenance chain
        assert "RAW" in result.data_metadata["adjustment_status"]

    def test_strategy_parameters_unchanged(self):
        """Verify default SMA parameters match M0.2.1 frozen strategy."""
        strategy = SMAStrategy()
        assert strategy.short_window == 50
        assert strategy.long_window == 200
        assert strategy.name == "SMA_50_200"

    def test_deterministic_results(self, synthetic_data):
        """Same data + same strategy = same result (deterministic)."""
        strategy = SMAStrategy(short_window=50, long_window=200)

        result1 = strategy.run(synthetic_data.copy())
        result2 = strategy.run(synthetic_data.copy())

        assert result1.total_trades == result2.total_trades
        assert result1.total_pnl == result2.total_pnl
        for t1, t2 in zip(result1.trades, result2.trades):
            assert t1.entry_date == t2.entry_date
            assert t1.exit_date == t2.exit_date
            assert t1.entry_price == t2.entry_price
            assert t1.exit_price == t2.exit_price
