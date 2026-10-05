"""
Fetch real NSE OHLCV data via yfinance and save as CSV.

Usage:
    python scripts/fetch_nse_data.py --symbol RELIANCE --years 5
    python scripts/fetch_nse_data.py --symbol RELIANCE --start 2020-01-01 --end 2025-12-31

Outputs a CSV with columns matching CsvDataSource expectations:
    trading_date, symbol, open, high, low, close, volume
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd


def fetch_nse_ohlcv(
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """
    Download OHLCV data for an NSE-listed stock from Yahoo Finance.

    Parameters
    ----------
    symbol : str
        NSE ticker (e.g. 'RELIANCE'). '.NS' suffix is appended automatically.
    start, end : date
        Date range (inclusive).

    Returns
    -------
    DataFrame with columns: trading_date, symbol, open, high, low, close, volume
    """
    yf_symbol = f"{symbol}.NS"
    ticker = yf.Ticker(yf_symbol)

    # yfinance 'end' is exclusive, so add one day
    df = ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )

    if df.empty:
        raise ValueError(f"No data returned for {yf_symbol} ({start} to {end})")

    # Normalize to our schema
    df = df.reset_index()
    df = df.rename(columns={
        "Date": "trading_date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })

    # Keep only the columns we need
    df = df[["trading_date", "open", "high", "low", "close", "volume"]].copy()
    df["symbol"] = symbol.upper()

    # Clean up dates — remove timezone info if present
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date

    # Drop any rows with NaN prices
    df = df.dropna(subset=["open", "high", "low", "close"])

    # Round prices to 2 decimal places
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].round(2)

    df["volume"] = df["volume"].astype(int)

    # Reorder columns
    df = df[["trading_date", "symbol", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("trading_date").reset_index(drop=True)

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Fetch NSE OHLCV data via Yahoo Finance."
    )
    parser.add_argument("--symbol", required=True, help="NSE ticker (e.g. RELIANCE)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--years", type=int, default=5,
        help="If --start/--end not given, fetch this many years back from today (default: 5)"
    )
    parser.add_argument(
        "--output-dir", default="data/raw",
        help="Output directory (default: data/raw)"
    )
    args = parser.parse_args()

    if args.end:
        end = date.fromisoformat(args.end)
    else:
        end = date.today()

    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = end - timedelta(days=args.years * 365)

    print(f"Fetching {args.symbol}.NS from {start} to {end}...")
    df = fetch_nse_ohlcv(args.symbol, start, end)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{args.symbol.upper()}_{start}_{end}.csv"
    output_path = output_dir / filename

    df.to_csv(output_path, index=False)
    print(f"Saved {len(df)} rows to {output_path}")
    print(f"Date range: {df['trading_date'].iloc[0]} to {df['trading_date'].iloc[-1]}")
    print(f"Price range: ₹{df['close'].min():.2f} — ₹{df['close'].max():.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
