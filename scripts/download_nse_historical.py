"""
NSE Historical Bhavcopy Downloader — Phase C.

Downloads NSE CM bhavcopies for a date range, extracts RELIANCE/EQ,
and builds a canonical NSE-native dataset.

This uses the existing NSEDataSource adapter's URL pattern and parse/normalize
logic. Raw zip files are preserved immutably.

Usage:
    python scripts/download_nse_historical.py --start 2024-01-02 --end 2026-10-15 --symbol RELIANCE
"""

import argparse
import hashlib
import io
import json
import logging
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# NSE URL pattern (UDiFF format, confirmed available from 2024-01-02)
BASE_URL = "https://nsearchives.nseindia.com/content/cm"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

# UDiFF column mapping (from ingestion/nse_source.py)
COLMAP_UDIFF = {
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "TtlTradgVol": "volume",
}


def bhavcopy_url(d: date) -> str:
    return f"{BASE_URL}/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def download_and_parse(session: requests.Session, d: date, raw_dir: Path,
                       symbol: str, series: str = "EQ"):
    """Download one bhavcopy, save raw zip, extract symbol rows.

    Returns:
        (DataFrame of matching rows, RawFileInfo dict) or (None, error_info)
    """
    url = bhavcopy_url(d)
    out_dir = raw_dir / "NSE" / f"{d:%Y}" / f"{d:%m}" / f"{d:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "original_file.zip"

    # Check if already downloaded (idempotent)
    if out_path.exists():
        content = out_path.read_bytes()
        file_info = {
            "date": d.isoformat(),
            "path": str(out_path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "source": "cache",
        }
    else:
        try:
            resp = session.get(url, timeout=30)
        except Exception as e:
            return None, {"date": d.isoformat(), "error": f"request_failed: {e}"}

        if resp.status_code == 404:
            return None, {"date": d.isoformat(), "status": 404, "reason": "not_found"}
        if resp.status_code != 200:
            return None, {"date": d.isoformat(), "status": resp.status_code, "reason": "http_error"}
        if not resp.content or resp.content[:2] != b"PK":
            return None, {"date": d.isoformat(), "status": resp.status_code, "reason": "not_zip"}

        content = resp.content
        out_path.write_bytes(content)
        file_info = {
            "date": d.isoformat(),
            "path": str(out_path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "source": "downloaded",
            "url": url,
        }

    # Parse
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return None, {"date": d.isoformat(), "error": "no_csv_in_zip"}

        with zf.open(csv_names[0]) as f:
            df = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"))

        # Normalize using UDiFF column mapping
        if "TckrSymb" not in df.columns:
            return None, {"date": d.isoformat(), "error": "not_udiff_format", "columns": list(df.columns[:5])}

        missing = [c for c in COLMAP_UDIFF if c not in df.columns]
        if missing:
            return None, {"date": d.isoformat(), "error": f"missing_columns: {missing}"}

        norm = df[list(COLMAP_UDIFF.keys())].rename(columns=COLMAP_UDIFF).copy()
        norm["trading_date"] = d
        norm["series"] = norm["series"].fillna("").astype(str).str.strip()

        for c in ["open", "high", "low", "close", "volume"]:
            norm[c] = pd.to_numeric(norm[c], errors="coerce")

        # Filter for target symbol/series
        mask = (norm["symbol"] == symbol) & (norm["series"] == series)
        rows = norm[mask].copy()

        file_info["total_rows"] = len(norm)
        file_info["symbol_rows"] = len(rows)

        return rows, file_info

    except Exception as e:
        return None, {"date": d.isoformat(), "error": f"parse_failed: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Download NSE historical bhavcopies")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbol", default="RELIANCE", help="Symbol (default: RELIANCE)")
    parser.add_argument("--series", default="EQ", help="Series (default: EQ)")
    parser.add_argument("--raw-dir", default="data/raw", help="Raw directory")
    parser.add_argument("--output-dir", default="data/nse_native", help="Output directory")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests (seconds)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*70}")
    print(f"NSE Historical Bhavcopy Download")
    print(f"{'='*70}")
    print(f"Symbol:    {args.symbol}/{args.series}")
    print(f"Period:    {start} -> {end}")
    print(f"Raw dir:   {raw_dir}")
    print(f"Output:    {output_dir}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"{'='*70}\n")

    session = requests.Session()
    session.headers.update(HEADERS)

    all_rows = []
    manifest = {
        "source": "NSE CM UDiFF Bhavcopy",
        "symbol": args.symbol,
        "series": args.series,
        "acquisition_start": datetime.now().isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "files": [],
        "errors": [],
        "stats": {},
    }

    d = start
    downloaded = 0
    cached = 0
    skipped_weekend = 0
    not_found = 0
    errors = 0

    while d <= end:
        if d.weekday() >= 5:  # Skip weekends
            skipped_weekend += 1
            d += timedelta(days=1)
            continue

        rows, info = download_and_parse(session, d, raw_dir, args.symbol, args.series)

        if rows is not None and len(rows) > 0:
            all_rows.append(rows)
            manifest["files"].append(info)
            if info.get("source") == "downloaded":
                downloaded += 1
            else:
                cached += 1

            if downloaded % 50 == 0 and downloaded > 0:
                logger.info(f"Progress: {downloaded} downloaded, {cached} cached, {not_found} not_found, date={d}")
        elif info.get("status") == 404 or info.get("reason") == "not_found":
            not_found += 1
            manifest["errors"].append(info)
        else:
            errors += 1
            manifest["errors"].append(info)
            logger.warning(f"Error on {d}: {info}")

        if info.get("source") == "downloaded":
            time.sleep(args.delay)

        d += timedelta(days=1)

    # Build canonical dataset
    if all_rows:
        dataset = pd.concat(all_rows, ignore_index=True)
        dataset = dataset.sort_values("trading_date").reset_index(drop=True)

        # Reorder columns
        cols = ["trading_date", "symbol", "series", "open", "high", "low", "close", "volume"]
        dataset = dataset[cols]

        # Save
        csv_path = output_dir / f"{args.symbol}_{args.series}_NSE_native.csv"
        dataset.to_csv(csv_path, index=False)

        csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

        manifest["stats"] = {
            "sessions": len(dataset),
            "first_date": str(dataset["trading_date"].iloc[0]),
            "last_date": str(dataset["trading_date"].iloc[-1]),
            "downloaded": downloaded,
            "cached": cached,
            "not_found_404": not_found,
            "errors": errors,
            "skipped_weekends": skipped_weekend,
            "dataset_sha256": csv_hash,
            "csv_path": str(csv_path),
        }
        manifest["acquisition_end"] = datetime.now().isoformat()

        # Save manifest
        manifest_path = output_dir / f"{args.symbol}_{args.series}_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

        print(f"\n{'='*70}")
        print(f"RESULTS")
        print(f"{'='*70}")
        print(f"Sessions:     {len(dataset)}")
        print(f"Date range:   {dataset['trading_date'].iloc[0]} -> {dataset['trading_date'].iloc[-1]}")
        print(f"Downloaded:   {downloaded}")
        print(f"Cached:       {cached}")
        print(f"Not found:    {not_found}")
        print(f"Errors:       {errors}")
        print(f"Dataset CSV:  {csv_path}")
        print(f"Dataset hash: {csv_hash}")
        print(f"Manifest:     {manifest_path}")
        print(f"{'='*70}")
    else:
        print("\nNO DATA RETRIEVED")
        manifest["acquisition_end"] = datetime.now().isoformat()

    return 0


if __name__ == "__main__":
    sys.exit(main())
