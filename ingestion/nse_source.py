"""
NSEDataSource — adapter for downloading, parsing, and normalizing
NSE Capital Market (CM) Bhavcopy data (UDiFF format).

Design note: this is deliberately an adapter, not a hard-coded script.
NSE has changed the bhavcopy delivery format/URL before (most recently
2024-07-08), and has an Extranet-only change scheduled for 2026-10-12
(circular NSE/MSD/74764) that discontinues some older broadcast/Extranet
modes for members. If the public download URL or file format changes
again, only this file needs to change — parse()/normalize() isolate the
rest of the pipeline (validators, db, report) from the source format.

Labeling per team convention:
  FACT       — the URL pattern below matched NSE's public bhavcopy since
               2024-07-08 (confirmed via NSE circulars + community reports).
  ASSUMPTION — this public nsearchives endpoint is unaffected by the
               2026-10-12 Extranet .DAT change, which as published applies
               to member-only Extranet paths (/cmftp/common/bhavcopy etc.),
               not the public website's report. Unverified against a live
               fetch — this environment cannot reach nseindia.com to check.
               If ingest.py starts returning 404/blocked around that date,
               check https://www.nseindia.com/all-reports for the current
               public link before assuming the adapter is broken.
"""

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class NSEDownloadError(Exception):
    """Raised when the bhavcopy file cannot be downloaded, is missing, or is invalid."""


@dataclass
class RawFile:
    path: Path
    sha256: str
    size_bytes: int


class NSEDataSource:
    """Adapter for NSE's CM-UDiFF Bhavcopy Final report."""

    BASE_URL = "https://nsearchives.nseindia.com/content/cm"
    HOME_URL = "https://www.nseindia.com"

    # NSE returns 403 without a browser-like User-Agent and without a
    # session cookie picked up from the homepage first. This is a known
    # quirk of their site, not a workaround of any access control meant
    # to keep the data private — bhavcopy is published for public download.
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/all-reports",
    }

    def __init__(self, raw_dir: Path):
        self.raw_dir = Path(raw_dir)
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update(self.HEADERS)
            resp = s.get(self.HOME_URL, timeout=15)
            resp.raise_for_status()
            self._session = s
        return self._session

    def _file_url(self, trading_date: date) -> str:
        ymd = trading_date.strftime("%Y%m%d")
        return f"{self.BASE_URL}/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"

    def download(self, trading_date: date) -> RawFile:
        """
        Download the bhavcopy zip for one trading day and store it
        immutably under raw_dir/NSE/YYYY/MM/DD/original_file.zip.
        Idempotent: if the raw file already exists, it is never
        overwritten — re-running ingest.py for the same date is safe.
        """
        out_dir = (
            self.raw_dir / "NSE"
            / f"{trading_date:%Y}" / f"{trading_date:%m}" / f"{trading_date:%d}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "original_file.zip"

        if out_path.exists():
            logger.info("Raw file already present, skipping download: %s", out_path)
            return self._describe(out_path)

        url = self._file_url(trading_date)
        session = self._get_session()
        resp = session.get(url, timeout=30)

        if resp.status_code == 404:
            raise NSEDownloadError(
                f"No bhavcopy found for {trading_date} (404). Likely a "
                f"weekend/holiday, or the file naming has changed — check "
                f"https://www.nseindia.com/all-reports."
            )
        resp.raise_for_status()

        if not resp.content or resp.content[:2] != b"PK":
            # A non-zip response (often an HTML error/interstitial page)
            # means we got blocked or the URL pattern is stale.
            raise NSEDownloadError(
                f"Response for {trading_date} was not a valid zip file. "
                f"NSE may have changed the URL/format, or the request was blocked."
            )

        out_path.write_bytes(resp.content)
        logger.info("Downloaded %s (%d bytes)", url, len(resp.content))
        return self._describe(out_path)

    @staticmethod
    def _describe(path: Path) -> RawFile:
        data = path.read_bytes()
        return RawFile(path=path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

    # ------------------------------------------------------------------

    def parse(self, raw_file: RawFile) -> pd.DataFrame:
        """Extract the CSV from the zip and load it, unmodified, into a DataFrame."""
        with zipfile.ZipFile(raw_file.path) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise NSEDownloadError(f"No CSV found inside {raw_file.path}")
            with zf.open(csv_names[0]) as f:
                df = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"))
        return df

    def normalize(self, df: pd.DataFrame, trading_date: date) -> pd.DataFrame:
        """
        Map NSE's raw column names to our canonical schema. Supports both
        the current UDiFF columns and the pre-2024-07-08 legacy columns,
        so old raw archives can still be reprocessed if we ever need to.
        """
        colmap_udiff = {
            "TckrSymb": "symbol",
            "SctySrs": "series",
            "ISIN": "isin",
            "OpnPric": "open",
            "HghPric": "high",
            "LwPric": "low",
            "ClsPric": "close",
            "LastPric": "last_price",
            "PrvsClsgPric": "previous_close",
            "TtlTradgVol": "volume",
            "TtlTrfVal": "traded_value",
        }
        colmap_legacy = {
            "SYMBOL": "symbol",
            "SERIES": "series",
            "ISIN": "isin",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "CLOSE": "close",
            "LAST": "last_price",
            "PREVCLOSE": "previous_close",
            "TOTTRDQTY": "volume",
            "TOTTRDVAL": "traded_value",
        }

        colmap = colmap_udiff if "TckrSymb" in df.columns else colmap_legacy
        missing = [c for c in colmap if c not in df.columns]
        if missing:
            raise NSEDownloadError(
                f"Expected columns missing from bhavcopy: {missing}. NSE may "
                f"have changed the file format — update the column mapping "
                f"in NSEDataSource.normalize()."
            )

        out = df[list(colmap.keys())].rename(columns=colmap).copy()
        out["trading_date"] = trading_date
        out["source"] = "NSE_CM_UDIFF"

        # NSE doesn't always populate the SctySrs field — some non-equity
        # instruments (bonds, debentures) have blank series.  Fill NaN with
        # an empty string so the (symbol, series) key is hashable and
        # consistent between upsert_securities() and insert_daily_prices().
        out["series"] = out["series"].fillna("").astype(str).str.strip()

        numeric_cols = [
            "open", "high", "low", "close", "last_price",
            "previous_close", "volume", "traded_value",
        ]
        for c in numeric_cols:
            out[c] = pd.to_numeric(out[c], errors="coerce")

        return out
