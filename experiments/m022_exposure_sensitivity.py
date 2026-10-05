"""
M0.2.2 -- Exposure Sensitivity Experiment.

Question:
    "How does position sizing affect the OBSERVED characteristics of
     our SMA 50/200 strategy and its Buy-and-Hold benchmark?"

This is NOT about finding the "better" sizing mode.  It isolates the
position-sizing effect by running FOUR variants on the SAME data:

    Variant 1: SMA 50/200, fixed 10 shares     (M0.2.1 replication)
    Variant 2: SMA 50/200, fixed 25% equity
    Variant 3: Buy & Hold, fixed 10 shares      (M0.2.1 replication)
    Variant 4: Buy & Hold, fixed 25% equity

All four use the same RELIANCE CSV from M0.2.1 to ensure comparability.

Reported metrics per the team lead's directive:
    exposure %, CAGR, volatility, Sharpe, max drawdown,
    turnover, trades, costs, final equity

Also includes: explicit Buy & Hold implementation documentation.

Usage:
    python experiments/m022_exposure_sensitivity.py
    python experiments/m022_exposure_sensitivity.py --csv data/raw/RELIANCE_2021-09-27_2026-09-24.csv --symbol RELIANCE
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.core.costs import PercentageCostModel
from backtesting.core.data import CsvDataSource
from backtesting.core.engine import BacktestResult, run_backtest
from backtesting.core.metrics import BacktestMetrics
from backtesting.strategies.sma_crossover import SmaCrossover
from backtesting.strategies.buy_and_hold import BuyAndHold


# ── Configuration ───────────────────────────────────────────────────
# Same parameters as M0.2.1 for replicability.
INITIAL_CAPITAL = 100_000.0
COST_RATE = 0.001            # 0.1% flat
FAST_SMA = 50
SLOW_SMA = 200

# The two sizing modes under test.
FIXED_QTY = 10               # Variant A: same as M0.2.1
FIXED_FRACTION = 0.25         # Variant B: 25% of available equity

# Default data: the exact CSV from M0.2.1.
DEFAULT_CSV = "data/raw/RELIANCE_2021-09-27_2026-09-24.csv"
DEFAULT_SYMBOL = "RELIANCE"


def run_variant(
    data_source: CsvDataSource,
    strategy,
    symbol: str,
    trade_qty: int | None = None,
    trade_fraction: float | None = None,
) -> BacktestResult:
    """Run one variant and return the result."""
    return run_backtest(
        data_source=data_source,
        signal_generator=strategy,
        symbol=symbol,
        initial_capital=INITIAL_CAPITAL,
        trade_qty=trade_qty,
        trade_fraction=trade_fraction,
        cost_model=PercentageCostModel(rate=COST_RATE),
    )


def format_metrics_row(label: str, m: BacktestMetrics) -> str:
    """Format one row of the comparison table."""
    return (
        f"  {label:<32} "
        f"{m.avg_exposure_pct:>8.2f}% "
        f"{m.cagr_pct:>+8.2f}% "
        f"{m.annualized_volatility_pct:>8.2f}% "
        f"{m.sharpe_ratio:>8.2f} "
        f"{m.max_drawdown_pct:>8.2f}% "
        f"{m.turnover:>8.4f}x "
        f"{m.total_trades:>6d} "
        f"{m.total_costs:>10.2f} "
        f"{m.final_equity:>14,.2f}"
    )


def format_header() -> str:
    """Format the table header."""
    return (
        f"  {'Variant':<32} "
        f"{'Exposure':>9} "
        f"{'CAGR':>9} "
        f"{'Vol(ann)':>9} "
        f"{'Sharpe':>8} "
        f"{'MaxDD':>9} "
        f"{'Turnover':>9} "
        f"{'Trades':>6} "
        f"{'Costs':>10} "
        f"{'Final Equity':>14}"
    )


def format_bnh_documentation(result: BacktestResult) -> str:
    """Document the exact Buy & Hold implementation for audit."""
    t = result.trade_log[0] if result.trade_log else None

    lines = [
        "",
        "=" * 80,
        "BUY & HOLD IMPLEMENTATION DOCUMENTATION",
        "=" * 80,
        "",
        "Strategy class:    backtesting.strategies.buy_and_hold.BuyAndHold",
        "Signal logic:      Emits BUY on bar 0, HOLD on all subsequent bars.",
        "                   Never emits SELL.",
        "",
        "Execution path:",
        "  1. Bar 0: BuyAndHold.generate() sets signal=BUY on this bar.",
        "  2. ExecutionSimulator sees BUY signal, sets pending_action='BUY'.",
        "  3. Bar 1: Pending BUY is filled at Bar 1's OPEN price.",
        "     This is next-bar-open execution, preventing look-ahead bias.",
        "  4. Bars 2..N: No further signals. Position is held to end.",
        "     No SELL signal is ever emitted, so the position is never closed.",
        "",
    ]

    if t:
        lines.extend([
            "Actual fill details (from M0.2.1 data):",
            f"  Purchase date:   {t.date}",
            f"  Fill price:      {t.price:,.2f} (Bar 1 open)",
            f"  Quantity:        {t.quantity} shares",
            f"  Total cost:      {t.cost:,.4f} ({COST_RATE*100:.1f}% of notional)",
            f"  Capital deployed:{t.price * t.quantity:,.2f}",
            f"  Cash remaining:  {INITIAL_CAPITAL - t.price * t.quantity - t.cost:,.2f}",
            f"  Exposure:        {t.price * t.quantity / INITIAL_CAPITAL * 100:.2f}% of capital",
            "",
            "What is NOT included:",
            "  - Dividends: NOT modeled. Equity tracks price only.",
            "  - Corporate actions: NOT adjusted. Using yfinance data as-is.",
            "    RELIANCE had a 1:1 bonus in Sep 2023. yfinance may or may",
            "    not adjust for this -- needs verification (see M0.2.3).",
            "  - Rebalancing: None. The 10-share position is held forever.",
            "  - Additional buys: None. Only the initial purchase.",
            "",
            "CRITICAL NOTE for benchmark comparisons:",
            f"  Both SMA and B&H used trade_qty={FIXED_QTY} in M0.2.1.",
            f"  Both deployed ~{t.price * t.quantity / INITIAL_CAPITAL * 100:.0f}% of capital.",
            f"  The M0.2.1 comparison was exposure-matched.",
            f"  The 0.64pp return difference was NOT caused by different",
            f"  capital deployment. It was caused by the SMA strategy",
            f"  entering/exiting at different times.",
        ])
    else:
        lines.append("  ERROR: No trades found in B&H result.")

    lines.extend(["", "=" * 80])
    return "\n".join(lines)


def format_trade_log(label: str, result: BacktestResult) -> str:
    """Format trade log for a variant."""
    lines = [
        f"",
        f"  {label} TRADE LOG ({len(result.trade_log)} fills):",
        f"  {'Date':<14} {'Side':<6} {'Qty':>8} {'Price':>12} {'Cost':>10} {'P&L':>14}",
        f"  {'-'*14} {'-'*6} {'-'*8} {'-'*12} {'-'*10} {'-'*14}",
    ]
    for t in result.trade_log:
        pnl_str = f"{t.pnl:>12,.2f}" if t.pnl is not None else "           --"
        lines.append(
            f"  {str(t.date):<14} {t.side:<6} {t.quantity:>8} "
            f"{t.price:>10,.2f}   {t.cost:>8,.2f}   {pnl_str}"
        )
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="M0.2.2 -- Exposure sensitivity: fixed-qty vs fixed-fraction"
    )
    parser.add_argument(
        "--csv", default=DEFAULT_CSV,
        help=f"OHLCV CSV (default: {DEFAULT_CSV})"
    )
    parser.add_argument(
        "--symbol", default=DEFAULT_SYMBOL,
        help=f"Ticker (default: {DEFAULT_SYMBOL})"
    )
    parser.add_argument(
        "--output-dir", default="experiments/results",
        help="Output directory"
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        print(f"M0.2.2 requires the same data file from M0.2.1.")
        print(f"Expected: {DEFAULT_CSV}")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    symbol = args.symbol.upper()
    data_source = CsvDataSource(csv_path)

    sma = SmaCrossover(fast_period=FAST_SMA, slow_period=SLOW_SMA)
    bnh = BuyAndHold()

    # ── Run all four variants ───────────────────────────────────────
    print("M0.2.2 -- Exposure Sensitivity Experiment")
    print(f"Data: {csv_path}")
    print(f"Symbol: {symbol}")
    print()

    print("Running Variant 1: SMA 50/200, 10 shares...")
    v1 = run_variant(data_source, sma, symbol, trade_qty=FIXED_QTY)

    print("Running Variant 2: SMA 50/200, 25% equity...")
    v2 = run_variant(data_source, sma, symbol, trade_fraction=FIXED_FRACTION)

    print("Running Variant 3: Buy & Hold, 10 shares...")
    v3 = run_variant(data_source, bnh, symbol, trade_qty=FIXED_QTY)

    print("Running Variant 4: Buy & Hold, 25% equity...")
    v4 = run_variant(data_source, bnh, symbol, trade_fraction=FIXED_FRACTION)

    # ── Build report ────────────────────────────────────────────────
    divider = "=" * 130

    report_lines = [
        divider,
        "M0.2.2 -- EXPOSURE SENSITIVITY EXPERIMENT",
        divider,
        "",
        f"  Question: How does position sizing affect the OBSERVED characteristics",
        f"            of SMA 50/200 and Buy & Hold on the same data?",
        "",
        f"  Data:            {csv_path}",
        f"  Symbol:          {symbol}",
        f"  Period:          {v1.start_date} to {v1.end_date}",
        f"  Initial capital: {INITIAL_CAPITAL:,.2f}",
        f"  Cost model:      {COST_RATE*100:.1f}% flat per trade",
        f"  SMA parameters:  {FAST_SMA}/{SLOW_SMA} (same as M0.2.1)",
        "",
        divider,
        format_header(),
        divider,
        format_metrics_row("SMA 50/200, 10 shares", v1.metrics),
        format_metrics_row(f"SMA 50/200, {FIXED_FRACTION:.0%} equity", v2.metrics),
        "",
        format_metrics_row("Buy & Hold, 10 shares", v3.metrics),
        format_metrics_row(f"Buy & Hold, {FIXED_FRACTION:.0%} equity", v4.metrics),
        divider,
        "",
        "  OBSERVATIONS (descriptive, not conclusions):",
        divider,
    ]

    # Compute and report the position-sizing effect on SMA
    sma_return_diff = v2.metrics.total_return_pct - v1.metrics.total_return_pct
    sma_sharpe_diff = v2.metrics.sharpe_ratio - v1.metrics.sharpe_ratio
    sma_dd_diff = v2.metrics.max_drawdown_pct - v1.metrics.max_drawdown_pct
    sma_exp_diff = v2.metrics.avg_exposure_pct - v1.metrics.avg_exposure_pct

    bnh_return_diff = v4.metrics.total_return_pct - v3.metrics.total_return_pct
    bnh_sharpe_diff = v4.metrics.sharpe_ratio - v3.metrics.sharpe_ratio
    bnh_dd_diff = v4.metrics.max_drawdown_pct - v3.metrics.max_drawdown_pct
    bnh_exp_diff = v4.metrics.avg_exposure_pct - v3.metrics.avg_exposure_pct

    report_lines.extend([
        "",
        "  SMA 50/200: effect of changing from 10 shares to 25% equity:",
        f"    Exposure changed by:  {sma_exp_diff:+.2f}pp",
        f"    Return changed by:    {sma_return_diff:+.2f}pp",
        f"    Sharpe changed by:    {sma_sharpe_diff:+.2f}",
        f"    Max DD changed by:    {sma_dd_diff:+.2f}pp",
        "",
        "  Buy & Hold: effect of changing from 10 shares to 25% equity:",
        f"    Exposure changed by:  {bnh_exp_diff:+.2f}pp",
        f"    Return changed by:    {bnh_return_diff:+.2f}pp",
        f"    Sharpe changed by:    {bnh_sharpe_diff:+.2f}",
        f"    Max DD changed by:    {bnh_dd_diff:+.2f}pp",
        "",
        "  Strategy comparison at MATCHED exposure (25% equity):",
        f"    SMA return:           {v2.metrics.total_return_pct:+.2f}%",
        f"    B&H return:           {v4.metrics.total_return_pct:+.2f}%",
        f"    Difference:           {v2.metrics.total_return_pct - v4.metrics.total_return_pct:+.2f}pp",
        f"    SMA Sharpe:           {v2.metrics.sharpe_ratio:.2f}",
        f"    B&H Sharpe:           {v4.metrics.sharpe_ratio:.2f}",
        f"    Difference:           {v2.metrics.sharpe_ratio - v4.metrics.sharpe_ratio:+.2f}",
        "",
        "  NOTE: These are observations, not conclusions.",
        "  Changing position sizing changes EXPOSURE, not STRATEGY QUALITY.",
        divider,
    ])

    # Trade logs
    report_lines.append(format_trade_log("V1: SMA 10-share", v1))
    report_lines.append(format_trade_log("V2: SMA 25%-equity", v2))
    report_lines.append(format_trade_log("V3: B&H 10-share", v3))
    report_lines.append(format_trade_log("V4: B&H 25%-equity", v4))

    # B&H documentation (team lead's request)
    report_lines.append(format_bnh_documentation(v3))

    report = "\n".join(report_lines)
    print(f"\n{report}")

    # ── Save results ────────────────────────────────────────────────
    def serialize(label, r):
        return {
            "label": label,
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
        "experiment": "M0.2.2",
        "description": "Exposure sensitivity: how position sizing affects observed strategy characteristics",
        "parameters": {
            "initial_capital": INITIAL_CAPITAL,
            "cost_rate": COST_RATE,
            "fast_sma": FAST_SMA,
            "slow_sma": SLOW_SMA,
            "fixed_qty": FIXED_QTY,
            "fixed_fraction": FIXED_FRACTION,
        },
        "data_source": str(csv_path),
        "variants": [
            serialize("SMA_50_200_10shares", v1),
            serialize(f"SMA_50_200_{FIXED_FRACTION:.0%}equity", v2),
            serialize("Buy_and_Hold_10shares", v3),
            serialize(f"Buy_and_Hold_{FIXED_FRACTION:.0%}equity", v4),
        ],
    }

    json_path = output_dir / f"m022_{symbol}_{v1.start_date}_{v1.end_date}.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {json_path}")

    report_path = output_dir / f"m022_{symbol}_{v1.start_date}_{v1.end_date}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved to {report_path}")

    print("\nExperiment M0.2.2 complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
