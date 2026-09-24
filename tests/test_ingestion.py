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


# ======================================================================
# ENTERO series regression tests — permanent, per Team Lead directive.
#
# Background: during M0.1 live validation, we discovered that NSE lists
# the same symbol in multiple series (e.g. ENTERO in both BL and EQ) on
# the same trading date. The dedup key was corrected from
# (symbol, trading_date) to (symbol, series, trading_date).
#
# These tests ensure that fix is never reverted.
# ======================================================================

ENTERO_HEADER = (
    "TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
)


def _make_entero_zip(tmp_path: Path, rows: list) -> Path:
    """Create a zip containing a CSV with the given rows."""
    csv_content = ENTERO_HEADER + "\n".join(rows) + "\n"
    zip_path = tmp_path / "entero_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20260918_F_0000.csv", csv_content)
    return zip_path


def test_entero_different_series_both_valid(tmp_path):
    """ENTERO/BL and ENTERO/EQ on the same date must BOTH survive validation.

    This is the core regression test for the dedup-key fix. If the validator
    ever reverts to using just (symbol, trading_date) as the dedup key,
    one of these rows would be incorrectly dropped as a duplicate.
    """
    rows = [
        # ENTERO in BL series
        "ENTERO,BL,INE917M01018,1900.00,1925.00,1880.00,1910.00,1912.00,1895.00,1000,1.9e6",
        # ENTERO in EQ series — same symbol, same date, different series → valid
        "ENTERO,EQ,INE917M01018,1905.00,1930.00,1885.00,1915.00,1916.00,1900.00,5000,9.5e6",
    ]
    zip_path = _make_entero_zip(tmp_path, rows)
    raw_file = RawFile(path=zip_path, sha256="test-hash", size_bytes=zip_path.stat().st_size)

    source = NSEDataSource(raw_dir=tmp_path)
    trading_date = date(2026, 9, 18)

    raw_df = source.parse(raw_file)
    norm_df = source.normalize(raw_df, trading_date)
    result = validate(norm_df)

    assert result.rows_received == 2
    assert result.duplicates == 0, "BL and EQ are different series — neither is a duplicate"
    assert result.rows_accepted == 2
    assert result.rows_rejected == 0
    assert set(result.clean_df["series"]) == {"BL", "EQ"}
    # Both rows must be present
    entero_rows = result.clean_df[result.clean_df["symbol"] == "ENTERO"]
    assert len(entero_rows) == 2, "Both ENTERO/BL and ENTERO/EQ must survive"


def test_entero_same_series_is_duplicate(tmp_path):
    """ENTERO/EQ and ENTERO/EQ on the same date → one is a duplicate.

    This confirms that dedup still works correctly WITHIN the same series.
    """
    rows = [
        "ENTERO,EQ,INE917M01018,1905.00,1930.00,1885.00,1915.00,1916.00,1900.00,5000,9.5e6",
        # Exact same row — same symbol, same series, same date → duplicate
        "ENTERO,EQ,INE917M01018,1905.00,1930.00,1885.00,1915.00,1916.00,1900.00,5000,9.5e6",
    ]
    zip_path = _make_entero_zip(tmp_path, rows)
    raw_file = RawFile(path=zip_path, sha256="test-hash", size_bytes=zip_path.stat().st_size)

    source = NSEDataSource(raw_dir=tmp_path)
    trading_date = date(2026, 9, 18)

    raw_df = source.parse(raw_file)
    norm_df = source.normalize(raw_df, trading_date)
    result = validate(norm_df)

    assert result.rows_received == 2
    assert result.duplicates == 1, "Same symbol + same series + same date = duplicate"
    assert result.rows_accepted == 1
    assert result.rows_rejected == 1
    # The surviving row must be the first occurrence
    assert len(result.clean_df) == 1
    assert result.clean_df.iloc[0]["symbol"] == "ENTERO"
    assert result.clean_df.iloc[0]["series"] == "EQ"


# ======================================================================
# NaN-series regression test — permanent, per Team Lead directive.
#
# Background: during M0.1 DB validation, we discovered that 7 NSE debt
# instruments (bonds, debentures) have blank/missing series in the source
# CSV.  Python's float('nan') != float('nan') caused a lookup mismatch
# between upsert_securities() and insert_daily_prices(), silently
# dropping 7 records.
#
# The fix: normalize() fills NaN series with "" via fillna("").
# This test ensures the normalized representation is deterministic
# and that such records survive the full pipeline.
# ======================================================================


def test_nan_series_normalized_to_empty_string(tmp_path):
    """Records with blank/missing series must normalize to '' (empty string).

    NSE debt instruments (bonds, NCDs) sometimes have no series field.
    The normalizer must produce a deterministic, hashable representation
    so (symbol, series) lookups work in the DB layer.

    Regression: float('nan') != float('nan') caused silent data loss.
    """
    rows = [
        # Bond with no series — SctySrs field is blank
        "865IRFC29,,INE053F07686,1110.00,1115.00,1100.00,1104.00,1105.00,1100.00,39,43056.00",
        # Normal equity with series
        "RELIANCE,EQ,INE002A01018,2900.00,2925.50,2890.00,2915.00,2916.00,2895.00,5000000,1.45e10",
        # Another bond with blank series
        "76NHAI31,,INE906B07EJ8,1112.00,1115.00,1105.00,1110.72,1111.00,1110.00,804,892298.88",
    ]
    csv_content = ENTERO_HEADER + "\n".join(rows) + "\n"
    zip_path = tmp_path / "nan_series_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20260918_F_0000.csv", csv_content)

    raw_file = RawFile(path=zip_path, sha256="test-hash", size_bytes=zip_path.stat().st_size)
    source = NSEDataSource(raw_dir=tmp_path)
    trading_date = date(2026, 9, 18)

    raw_df = source.parse(raw_file)
    norm_df = source.normalize(raw_df, trading_date)

    # Verify: no NaN in series column
    assert not norm_df["series"].isna().any(), (
        "series column must have no NaN values after normalization"
    )

    # Verify: blank series becomes empty string, not 'nan' or 'NaN'
    bond_rows = norm_df[norm_df["symbol"].isin(["865IRFC29", "76NHAI31"])]
    assert len(bond_rows) == 2, "Both bond rows should be present"
    for _, row in bond_rows.iterrows():
        assert row["series"] == "", (
            f"{row['symbol']}: series should be '' (empty string), "
            f"got '{row['series']}' (type: {type(row['series']).__name__})"
        )
        assert isinstance(row["series"], str), (
            f"{row['symbol']}: series must be str, not {type(row['series'])}"
        )

    # Verify: normal EQ series is preserved
    eq_row = norm_df[norm_df["symbol"] == "RELIANCE"].iloc[0]
    assert eq_row["series"] == "EQ"

    # Verify: all three rows pass validation
    result = validate(norm_df)
    assert result.rows_accepted == 3
    assert result.rows_rejected == 0
    assert result.duplicates == 0

    # Verify: (symbol, series) pairs are distinct and hashable
    pairs = set(zip(result.clean_df["symbol"], result.clean_df["series"]))
    assert len(pairs) == 3, "All three (symbol, series) pairs must be distinct"


def test_nan_series_dedup_works_correctly(tmp_path):
    """Two records with blank series and the same symbol are duplicates.

    This ensures dedup works correctly even when series is '' (empty string).
    """
    rows = [
        "865IRFC29,,INE053F07686,1110.00,1115.00,1100.00,1104.00,1105.00,1100.00,39,43056.00",
        # Same bond, same date, blank series → duplicate
        "865IRFC29,,INE053F07686,1110.00,1115.00,1100.00,1104.00,1105.00,1100.00,39,43056.00",
    ]
    csv_content = ENTERO_HEADER + "\n".join(rows) + "\n"
    zip_path = tmp_path / "nan_dup_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20260918_F_0000.csv", csv_content)

    raw_file = RawFile(path=zip_path, sha256="test-hash", size_bytes=zip_path.stat().st_size)
    source = NSEDataSource(raw_dir=tmp_path)
    norm_df = source.normalize(source.parse(raw_file), date(2026, 9, 18))
    result = validate(norm_df)

    assert result.rows_received == 2
    assert result.duplicates == 1
    assert result.rows_accepted == 1
    assert result.clean_df.iloc[0]["series"] == ""

