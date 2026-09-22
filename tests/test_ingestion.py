"""
Proves the parse -> normalize -> validate chain works correctly against
a synthetic UDiFF-format bhavcopy, without needing network access to NSE
or a live Postgres instance.

Deliberately injects known-bad rows (an OHLC violation, a duplicate, a
missing field) so the test fails loudly if the integrity checks ever stop
catching what they're supposed to catch.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ingestion.nse_source import NSEDataSource, RawFile
from ingestion.validators import validate

UDIFF_HEADER = (
    "TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
)

# 5 clean rows, 1 duplicate of row 1, 1 OHLC violation (High < Close),
# 1 missing close price.
UDIFF_ROWS = [
    "RELIANCE,EQ,INE002A01018,2900.00,2925.50,2890.00,2915.00,2916.00,2895.00,5000000,1.45e10",
    "TCS,EQ,INE467B01029,3800.00,3825.00,3780.00,3810.00,3811.00,3795.00,2000000,7.6e9",
    "INFY,EQ,INE009A01021,1500.00,1515.00,1490.00,1505.00,1506.00,1498.00,3000000,4.5e9",
    "RELIANCE,EQ,INE002A01018,2900.00,2925.50,2890.00,2915.00,2916.00,2895.00,5000000,1.45e10",  # duplicate
    "BADHIGH,EQ,INE999Z99999,100.00,105.00,98.00,110.00,110.00,99.00,10000,1.0e6",  # High(105) < Close(110)
    "MISSCLOSE,EQ,INE888Y88888,50.00,55.00,49.00,,52.00,50.00,20000,1.0e6",  # missing close
]


def _make_zip(tmp_path: Path) -> Path:
    csv_content = UDIFF_HEADER + "\n".join(UDIFF_ROWS) + "\n"
    zip_path = tmp_path / "original_file.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20260101_F_0000.csv", csv_content)
    return zip_path


def test_parse_normalize_validate(tmp_path):
    zip_path = _make_zip(tmp_path)
    raw_file = RawFile(path=zip_path, sha256="test-hash", size_bytes=zip_path.stat().st_size)

    source = NSEDataSource(raw_dir=tmp_path)
    trading_date = date(2026, 1, 1)

    raw_df = source.parse(raw_file)
    assert len(raw_df) == 6  # 6 data rows written above

    norm_df = source.normalize(raw_df, trading_date)
    assert set(["symbol", "open", "high", "low", "close", "volume"]).issubset(norm_df.columns)
    assert (norm_df["trading_date"] == trading_date).all()

    result = validate(norm_df)

    assert result.rows_received == 6
    assert result.duplicates == 1
    assert result.ohlc_violations == 1
    assert result.missing_values == 1
    # 6 received - 1 duplicate - 1 ohlc violation - 1 missing = 3 clean rows
    assert result.rows_accepted == 3
    assert result.rows_rejected == 3
    assert set(result.clean_df["symbol"]) == {"RELIANCE", "TCS", "INFY"}


def test_raw_file_never_overwritten(tmp_path):
    """download() must be idempotent: an existing raw file is reused, not re-fetched."""
    out_dir = tmp_path / "NSE" / "2026" / "01" / "01"
    out_dir.mkdir(parents=True)
    existing = out_dir / "original_file.zip"
    existing.write_bytes(b"PK\x03\x04sentinel-content")

    source = NSEDataSource(raw_dir=tmp_path)
    result = source.download(date(2026, 1, 1))

    assert result.path == existing
    assert existing.read_bytes() == b"PK\x03\x04sentinel-content"  # untouched
