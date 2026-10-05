"""
M0.2.1 — First real-data experiment.

Runs two experiments side-by-side on the same real NSE data:
    Experiment A: 50/200 SMA crossover (unoptimized baseline)
    Experiment B: Buy-and-hold (benchmark)

Then prints a structured comparison and writes a detailed results file.

The question we're answering:
    "What happens when our completely unoptimized baseline is applied
     to real historical NSE data?"

Usage:
    python experiments/m021_first_real_data.py --symbol RELIANCE
    python experiments/m021_first_real_data.py --symbol RELIANCE --years 7
    python experiments/m021_first_real_data.py --csv data/raw/RELIANCE_2021-09-25_2026-09-24.csv --symbol RELIANCE
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.core.costs import PercentageCostModel
from backtesting.core.data import CsvDataSource, DataFrameSource
from backtesting.core.engine import BacktestResult, run_backtest
from backtesting.core.metrics import BacktestMetrics
from backtesting.strategies.sma_crossover import SmaCrossover
from backtesting.strategies.buy_and_hold import BuyAndHold


# ── Configuration ───────────────────────────────────────────────────
# All parameters are UNOPTIMIZED defaults — that's the point of M0.2.1.
INITIAL_CAPITAL = 100_000.0
TRADE_QTY = 10          # shares per BUY signal
COST_RATE = 0.001        # 0.1% flat (rough Indian discount broker)
FAST_SMA = 50
SLOW_SMA = 200
DEFAULT_YEARS = 5


def fetch_data(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Download OHLCV via yfinance. Returns DataFrame in our canonical schema."""
    import yfinance as yf

    yf_symbol = f"{symbol}.NS"
    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {yf_symbol} ({start} --> {end})")

    df = df.reset_index()
    df = df.rename(columns={
        "Date": "trading_date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    df = df[["trading_date", "open", "high", "low", "close", "volume"]].copy()
    df["symbol"] = symbol.upper()
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df = df.dropna(subset=["open", "high", "low", "close"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].round(2)
    df["volume"] = df["volume"].astype(int)
    df = df[["trading_date", "symbol", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("trading_date").reset_index(drop=True)
    return df


def run_experiment(
    data_source,
    strategy,
    symbol: str,
    initial_capital: float,
    trade_qty: int,
    cost_model,
) -> BacktestResult:
    """Run one experiment and return the result."""
    return run_backtest(
        data_source=data_source,
        signal_generator=strategy,
        symbol=symbol,
        initial_capital=initial_capital,
        trade_qty=trade_qty,
        cost_model=cost_model,
    )


def format_comparison(result_a: BacktestResult, result_b: BacktestResult) -> str:
    """Format a side-by-side comparison report."""
    ma = result_a.metrics
    mb = result_b.metrics

    divider = "=" * 70

    lines = [
        divider,
        "M0.2.1 — FIRST REAL-DATA EXPERIMENT",
        divider,
        "",
        f"  Symbol:           {result_a.symbol}",
        f"  Period:           {result_a.start_date} --> {result_a.end_date}",
        f"  Initial capital:  ₹{ma.initial_capital:,.2f}",
        f"  Trade size:       {TRADE_QTY} shares per signal",
        f"  Cost model:       {COST_RATE*100:.1f}% flat per trade",
        "",
        divider,
        f"  {'Metric':<24} {'A: SMA 50/200':>18} {'B: Buy & Hold':>18}",
        divider,
        "",
        f"  {'Final equity':<24} {'₹' + f'{ma.final_equity:,.2f}':>18} {'₹' + f'{mb.final_equity:,.2f}':>18}",
        f"  {'Total return':<24} {ma.total_return_pct:>+17.2f}% {mb.total_return_pct:>+17.2f}%",
        f"  {'CAGR':<24} {ma.cagr_pct:>+17.2f}% {mb.cagr_pct:>+17.2f}%",
        f"  {'Sharpe ratio':<24} {ma.sharpe_ratio:>18.2f} {mb.sharpe_ratio:>18.2f}",
        f"  {'Max drawdown':<24} {ma.max_drawdown_pct:>17.2f}% {mb.max_drawdown_pct:>17.2f}%",
        "",
        f"  {'Total trades':<24} {ma.total_trades:>18d} {mb.total_trades:>18d}",
        f"  {'Win rate':<24} {ma.win_rate_pct:>17.2f}% {mb.win_rate_pct:>17.2f}%",
        f"  {'Profit factor':<24} {ma.profit_factor:>18.2f} {mb.profit_factor:>18.2f}",
        "",
        divider,
        "  VERDICT",
        divider,
    ]

    # Compare total returns
    diff_return = ma.total_return_pct - mb.total_return_pct
    diff_sharpe = ma.sharpe_ratio - mb.sharpe_ratio
    diff_dd = ma.max_drawdown_pct - mb.max_drawdown_pct  # less negative = better

    if diff_return > 0:
        lines.append(f"  SMA 50/200 outperformed Buy & Hold by {diff_return:+.2f}% total return.")
    elif diff_return < 0:
        lines.append(f"  SMA 50/200 underperformed Buy & Hold by {diff_return:+.2f}% total return.")
    else:
        lines.append(f"  SMA 50/200 matched Buy & Hold on total return.")

    if diff_sharpe > 0:
        lines.append(f"  SMA 50/200 had better risk-adjusted return (Sharpe {diff_sharpe:+.2f}).")
    elif diff_sharpe < 0:
        lines.append(f"  SMA 50/200 had worse risk-adjusted return (Sharpe {diff_sharpe:+.2f}).")

    # Drawdown: less negative is better
    if diff_dd > 0:
        lines.append(f"  SMA 50/200 had a shallower max drawdown (better by {diff_dd:+.2f}pp).")
    elif diff_dd < 0:
        lines.append(f"  SMA 50/200 had a deeper max drawdown (worse by {diff_dd:+.2f}pp).")

    lines.append("")
    lines.append("  NOTE: This is the RAW, UNOPTIMIZED result. No parameter tuning,")
    lines.append("  no walk-forward, no corporate-action adjustment. This is the")
    lines.append("  baseline truth before any optimization.")
    lines.append(divider)
    lines.append("")

    # ── Trade log summary for Experiment A ──
    if result_a.trade_log:
        lines.append(f"  SMA 50/200 TRADE LOG ({len(result_a.trade_log)} fills):")
        lines.append(f"  {'Date':<14} {'Side':<6} {'Qty':>5} {'Price':>12} {'Cost':>10} {'P&L':>12}")
        lines.append(f"  {'-'*14} {'-'*6} {'-'*5} {'-'*12} {'-'*10} {'-'*12}")
        for t in result_a.trade_log:
            pnl_str = f"₹{t.pnl:,.2f}" if t.pnl is not None else "—"
            lines.append(
                f"  {str(t.date):<14} {t.side:<6} {t.quantity:>5} "
                f"₹{t.price:>10,.2f} ₹{t.cost:>8,.2f} {pnl_str:>12}"
            )
    else:
        lines.append("  SMA 50/200: No trades executed.")

    lines.append("")
    lines.append(divider)
    return "\n".join(lines)


def save_results_json(
    result_a: BacktestResult,
    result_b: BacktestResult,
    output_path: Path,
) -> None:
    """Save machine-readable results for downstream analysis."""
    def serialize_result(r: BacktestResult) -> dict:
        return {
            "strategy": r.strategy_name,
            "symbol": r.symbol,
            "start_date": str(r.start_date),
            "end_date": str(r.end_date),
            "metrics": asdict(r.metrics),
            "num_trades": len(r.trade_log),
            "trades": [
                {
                    "date": str(t.date),
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "cost": t.cost,
                    "pnl": t.pnl,
                }
                for t in r.trade_log
            ],
        }

    results = {
        "experiment": "M0.2.1",
        "description": "First real-data experiment: unoptimized SMA 50/200 vs Buy-and-Hold",
        "parameters": {
            "initial_capital": INITIAL_CAPITAL,
            "trade_qty": TRADE_QTY,
            "cost_rate": COST_RATE,
            "fast_sma": FAST_SMA,
            "slow_sma": SLOW_SMA,
        },
        "experiment_a": serialize_result(result_a),
        "experiment_b": serialize_result(result_b),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="M0.2.1 — First real-data experiment: SMA 50/200 vs Buy-and-Hold"
    )
    parser.add_argument(
        "--csv", default=None,
        help="Path to pre-downloaded OHLCV CSV. If omitted, fetches via yfinance."
    )
    parser.add_argument("--symbol", default="RELIANCE", help="NSE ticker (default: RELIANCE)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--years", type=int, default=DEFAULT_YEARS,
        help=f"Years of history if --start/--end omitted (default: {DEFAULT_YEARS})"
    )
    parser.add_argument(
        "--output-dir", default="experiments/results",
        help="Directory for output files"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Get data ─────────────────────────────────────────────────
    if args.csv:
        print(f"Loading data from {args.csv}...")
        data_source = CsvDataSource(args.csv)
        csv_path = Path(args.csv)
    else:
        if args.end:
            end = date.fromisoformat(args.end)
        else:
            end = date.today()
        if args.start:
            start = date.fromisoformat(args.start)
        else:
            start = end - timedelta(days=args.years * 365)

        print(f"Fetching {args.symbol}.NS from Yahoo Finance ({start} --> {end})...")
        df = fetch_data(args.symbol, start, end)

        # Save a copy for reproducibility
        csv_filename = f"{args.symbol.upper()}_{df['trading_date'].iloc[0]}_{df['trading_date'].iloc[-1]}.csv"
        csv_path = Path("data/raw") / csv_filename
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)
        print(f"Saved {len(df)} rows to {csv_path}")

        data_source = CsvDataSource(csv_path)

    # ── 2. Configure experiments ────────────────────────────────────
    cost_model = PercentageCostModel(rate=COST_RATE)

    strategy_a = SmaCrossover(fast_period=FAST_SMA, slow_period=SLOW_SMA)
    strategy_b = BuyAndHold()

    symbol = args.symbol.upper()

    # ── 3. Run Experiment A: SMA 50/200 ─────────────────────────────
    print(f"\nRunning Experiment A: {strategy_a.name}...")
    result_a = run_experiment(
        data_source=data_source,
        strategy=strategy_a,
        symbol=symbol,
        initial_capital=INITIAL_CAPITAL,
        trade_qty=TRADE_QTY,
        cost_model=cost_model,
    )
    print(f"  --> {result_a.metrics.total_return_pct:+.2f}% return, "
          f"{len(result_a.trade_log)} trades")

    # ── 4. Run Experiment B: Buy-and-Hold ───────────────────────────
    print(f"Running Experiment B: {strategy_b.name}...")
    result_b = run_experiment(
        data_source=data_source,
        strategy=strategy_b,
        symbol=symbol,
        initial_capital=INITIAL_CAPITAL,
        trade_qty=TRADE_QTY,
        cost_model=cost_model,
    )
    print(f"  --> {result_b.metrics.total_return_pct:+.2f}% return, "
          f"{len(result_b.trade_log)} trades")

    # ── 5. Print comparison report ──────────────────────────────────
    report = format_comparison(result_a, result_b)
    print(f"\n{report}")

    # ── 6. Save machine-readable results ────────────────────────────
    json_path = output_dir / f"m021_{symbol}_{result_a.start_date}_{result_a.end_date}.json"
    save_results_json(result_a, result_b, json_path)
    print(f"Results saved to {json_path}")

    # ── 7. Save text report ─────────────────────────────────────────
    report_path = output_dir / f"m021_{symbol}_{result_a.start_date}_{result_a.end_date}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved to {report_path}")

    # ── 8. Save data source path for audit trail ────────────────────
    print(f"\nData source: {csv_path}")
    print("Experiment M0.2.1 complete.")


if __name__ == "__main__":
    main()
