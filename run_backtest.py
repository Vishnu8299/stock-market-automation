"""
CLI entry point for running a backtest.

Usage:
    python run_backtest.py --csv data/prices.csv --symbol RELIANCE \\
        --start 2020-01-01 --end 2025-12-31 --capital 100000 --qty 10

Wires CsvDataSource + SmaCrossover + PercentageCostModel through the
backtesting engine and prints the report.
"""

import argparse
import sys
from datetime import date

from backtesting.core.costs import PercentageCostModel
from backtesting.core.data import CsvDataSource
from backtesting.core.engine import run_backtest
from backtesting.core.reporting import print_report
from backtesting.strategies.sma_crossover import SmaCrossover


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a 50/200 SMA crossover backtest on OHLCV data."
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to OHLCV CSV file (columns: date/trading_date, open, high, low, close, volume)"
    )
    parser.add_argument("--symbol", required=True, help="Ticker symbol")
    parser.add_argument(
        "--start", default=None,
        help="Start date (YYYY-MM-DD). Omit to use all available data."
    )
    parser.add_argument(
        "--end", default=None,
        help="End date (YYYY-MM-DD). Omit to use all available data."
    )
    parser.add_argument(
        "--capital", type=float, default=100_000.0,
        help="Initial capital (default: 100000)"
    )
    parser.add_argument(
        "--qty", type=int, default=10,
        help="Shares per BUY signal (default: 10)"
    )
    parser.add_argument(
        "--cost-rate", type=float, default=0.001,
        help="Transaction cost rate as a decimal (default: 0.001 = 0.1%%)"
    )
    parser.add_argument(
        "--fast-period", type=int, default=50,
        help="Fast SMA period (default: 50)"
    )
    parser.add_argument(
        "--slow-period", type=int, default=200,
        help="Slow SMA period (default: 200)"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None

    data_source = CsvDataSource(args.csv)
    strategy = SmaCrossover(fast_period=args.fast_period, slow_period=args.slow_period)
    cost_model = PercentageCostModel(rate=args.cost_rate)

    result = run_backtest(
        data_source=data_source,
        signal_generator=strategy,
        symbol=args.symbol,
        start=start,
        end=end,
        initial_capital=args.capital,
        trade_qty=args.qty,
        cost_model=cost_model,
    )

    print_report(
        metrics=result.metrics,
        strategy_name=result.strategy_name,
        symbol=result.symbol,
        start_date=str(result.start_date),
        end_date=str(result.end_date),
    )


if __name__ == "__main__":
    main()
