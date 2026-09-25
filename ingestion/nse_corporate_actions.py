"""
NSE Corporate Action Source Adapter — downloads, parses, normalizes, and
validates corporate action data from NSE India.

Architecture (M0.2.3 Hybrid, Team Lead approved):
    NSE Corporate Actions
            ↓
    Raw response/archive
            ↓
    Parser
            ↓
    Normalized CorporateAction
            ↓
    Validation
            ↓
    Versioned Event Log (corporate_actions table)
            ↓
    Adjustment Engine
            ↓
    Backtester

NSE Corporate Actions API:
    URL: https://www.nseindia.com/api/corporates-corporateActions
    Params: index=equities, from_date=DD-MM-YYYY, to_date=DD-MM-YYYY
    Session: Requires homepage cookie bootstrap (same as bhavcopy).

The 'subject' field is a free-text description that encodes the action:
    "Stock Split From Rs.10/- to Rs.2/-"       → SPLIT 1:5
    "Bonus issue 1:1"                           → BONUS 1:1
    "Dividend - Rs 20 Per Share"                → DIVIDEND ₹20
    "Final Dividend - Rs.18.50 Per Share"       → DIVIDEND ₹18.50
    "Interim Dividend - Rs 5 Per Share"         → DIVIDEND ₹5
    "Rights Issue 1:5 @ Rs 1000 Per Share"      → RIGHTS 1:5 @ ₹1000

IMPORTANT: raw daily_prices are NEVER modified. Corporate actions go into
the corporate_actions table. Adjusted prices are derived separately.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class CorporateActionParseError(Exception):
    """Raised when a corporate action cannot be parsed."""


# ======================================================================
# Subject-line parsers
# ======================================================================

def parse_split_subject(subject: str) -> Optional[Tuple[int, int]]:
    """Parse a split subject line into (ratio_from, ratio_to).

    Examples:
        "Stock Split From Rs.10/- to Rs.2/-"      → (1, 5)  [10/2=5]
        "Stock Split From Rs.10/- to Rs.5/-"       → (1, 2)  [10/5=2]
        "Stock Split From Rs 10 to Rs 1"           → (1, 10) [10/1=10]
        "Face Value Split Rs. 10 to Rs. 2"         → (1, 5)

    Returns (ratio_from, ratio_to) or None if not parseable.
    """
    subject = subject.strip()

    # Pattern: "From Rs.X to Rs.Y" or "Rs X to Rs Y"
    pattern = r'(?:from\s+)?rs\.?\s*(\d+(?:\.\d+)?)\s*/?\-?\s*(?:to|per share to)\s*rs\.?\s*(\d+(?:\.\d+)?)'
    match = re.search(pattern, subject, re.IGNORECASE)

    if match:
        old_fv = float(match.group(1))
        new_fv = float(match.group(2))
        if new_fv > 0 and old_fv > new_fv:
            ratio = old_fv / new_fv
            # Convert to integer ratio
            if ratio == int(ratio):
                return (1, int(ratio))
            # Handle non-integer ratios (e.g., 10/3)
            from fractions import Fraction
            frac = Fraction(old_fv).limit_denominator(100) / Fraction(new_fv).limit_denominator(100)
            return (frac.denominator, frac.numerator)

    # Pattern: "Split 1:5" or "Stock Split 1:5"
    pattern2 = r'split\s*(\d+)\s*[:/]\s*(\d+)'
    match2 = re.search(pattern2, subject, re.IGNORECASE)
    if match2:
        return (int(match2.group(1)), int(match2.group(2)))

    return None


def parse_bonus_subject(subject: str) -> Optional[Tuple[int, int]]:
    """Parse a bonus subject line into (ratio_from, ratio_to).

    Examples:
        "Bonus issue 1:1"                → (1, 1)
        "Bonus 2:1"                      → (2, 1) [1 new for every 2 held]
        "Bonus Issue 1:2"                → (1, 2) [2 new for every 1 held]

    Returns (ratio_from, ratio_to) or None if not parseable.

    Convention: ratio_from = existing shares, ratio_to = new shares
    """
    subject = subject.strip()

    pattern = r'bonus\s*(?:issue)?\s*(\d+)\s*[:/]\s*(\d+)'
    match = re.search(pattern, subject, re.IGNORECASE)

    if match:
        return (int(match.group(1)), int(match.group(2)))

    return None


def parse_dividend_subject(subject: str) -> Optional[Tuple[float, str]]:
    """Parse a dividend subject line into (amount_per_share, dividend_type).

    Examples:
        "Dividend - Rs 20 Per Share"           → (20.0, "REGULAR")
        "Final Dividend - Rs.18.50 Per Share"  → (18.50, "FINAL")
        "Interim Dividend - Rs 5 Per Share"    → (5.0, "INTERIM")
        "Special Dividend Rs.100/-"            → (100.0, "SPECIAL")
        "Dividend - Rs 7.50/- Per Share"       → (7.50, "REGULAR")
        "Dividend Rs.3.50 per share"           → (3.50, "REGULAR")

    Returns (amount, type) or None if not parseable.
    """
    subject = subject.strip()

    # Determine type
    div_type = "REGULAR"
    subject_lower = subject.lower()
    if "final" in subject_lower:
        div_type = "FINAL"
    elif "interim" in subject_lower:
        div_type = "INTERIM"
    elif "special" in subject_lower:
        div_type = "SPECIAL"

    # Pattern: "Rs.X" or "Rs X" or "Re.X" followed by optional /- or per share
    pattern = r'(?:rs|re)\.?\s*(\d+(?:\.\d+)?)\s*(?:/\-)?'
    match = re.search(pattern, subject, re.IGNORECASE)

    if match:
        amount = float(match.group(1))
        if amount > 0:
            return (amount, div_type)

    return None


def parse_rights_subject(subject: str) -> Optional[Tuple[int, int, float]]:
    """Parse a rights subject line into (ratio_from, ratio_to, price).

    Examples:
        "Rights Issue 1:5 @ Rs 1000 Per Share"  → (5, 1, 1000.0)
        "Rights 2:7 @ Rs.250"                   → (7, 2, 250.0)

    Returns (existing_shares, new_shares, issue_price) or None.
    """
    subject = subject.strip()

    pattern = r'rights?\s*(?:issue)?\s*(\d+)\s*[:/]\s*(\d+)\s*(?:@|at)\s*rs\.?\s*(\d+(?:\.\d+)?)'
    match = re.search(pattern, subject, re.IGNORECASE)

    if match:
        return (
            int(match.group(2)),  # existing shares (denominator)
            int(match.group(1)),  # new shares (numerator)
            float(match.group(3)),  # issue price
        )

    return None


# ======================================================================
# Classifier: determine action_type from subject line
# ======================================================================

def classify_action(subject: str) -> str:
    """Classify a corporate action subject line into action_type.

    Returns one of: 'SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS',
                    'MERGER', 'DEMERGER', 'SYMBOL_CHANGE', 'UNKNOWN'
    """
    s = subject.lower()

    if "split" in s:
        return "SPLIT"
    if "bonus" in s:
        return "BONUS"
    if "dividend" in s or "interim div" in s:
        return "DIVIDEND"
    if "right" in s:
        return "RIGHTS"
    if "merger" in s and "de" not in s:
        return "MERGER"
    if "demerger" in s or "de-merger" in s:
        return "DEMERGER"
    if "name change" in s or "symbol change" in s:
        return "SYMBOL_CHANGE"

    return "UNKNOWN"


# ======================================================================
# Normalized event builder
# ======================================================================

@dataclass
class ParsedCorporateAction:
    """A parsed and normalized corporate action ready for DB insertion."""
    symbol: str
    series: str
    action_type: str
    ex_date: date
    record_date: Optional[date]
    ratio_from: Optional[int] = None
    ratio_to: Optional[int] = None
    dividend_amount: Optional[float] = None
    dividend_type: Optional[str] = None
    rights_price: Optional[float] = None
    rights_ratio_from: Optional[int] = None
    rights_ratio_to: Optional[int] = None
    source: str = "NSE_CA"
    raw_subject: str = ""
    event_version: str = "1.0"
    parse_status: str = "OK"  # "OK", "PARTIAL", "FAILED"
    parse_notes: str = ""


def parse_nse_corporate_action(raw: dict) -> ParsedCorporateAction:
    """Parse a single raw NSE corporate action dict into a normalized event.

    Args:
        raw: dict with keys like 'symbol', 'series', 'subject', 'exDt', 'recDt', etc.
             This is the format returned by NSE's corporate actions page/API.

    Returns:
        ParsedCorporateAction with parse_status indicating success.
    """
    symbol = raw.get("symbol", "").strip()
    series = raw.get("series", "EQ").strip()
    subject = raw.get("subject", raw.get("purpose", "")).strip()

    # Parse dates (NSE uses DD-Mon-YYYY format)
    ex_date_str = raw.get("exDt", raw.get("ex_date", ""))
    record_date_str = raw.get("recDt", raw.get("record_date", ""))

    ex_dt = _parse_nse_date(ex_date_str)
    rec_dt = _parse_nse_date(record_date_str)

    if ex_dt is None:
        return ParsedCorporateAction(
            symbol=symbol, series=series, action_type="UNKNOWN",
            ex_date=date(1900, 1, 1), record_date=None,
            source="NSE_CA", raw_subject=subject,
            parse_status="FAILED",
            parse_notes=f"Could not parse ex_date: '{ex_date_str}'",
        )

    action_type = classify_action(subject)

    result = ParsedCorporateAction(
        symbol=symbol, series=series, action_type=action_type,
        ex_date=ex_dt, record_date=rec_dt,
        source="NSE_CA", raw_subject=subject,
    )

    if action_type == "SPLIT":
        parsed = parse_split_subject(subject)
        if parsed:
            result.ratio_from, result.ratio_to = parsed
        else:
            result.parse_status = "PARTIAL"
            result.parse_notes = f"Classified as SPLIT but could not parse ratio from: '{subject}'"

    elif action_type == "BONUS":
        parsed = parse_bonus_subject(subject)
        if parsed:
            result.ratio_from, result.ratio_to = parsed
        else:
            result.parse_status = "PARTIAL"
            result.parse_notes = f"Classified as BONUS but could not parse ratio from: '{subject}'"

    elif action_type == "DIVIDEND":
        parsed = parse_dividend_subject(subject)
        if parsed:
            result.dividend_amount, result.dividend_type = parsed
        else:
            result.parse_status = "PARTIAL"
            result.parse_notes = f"Classified as DIVIDEND but could not parse amount from: '{subject}'"

    elif action_type == "RIGHTS":
        parsed = parse_rights_subject(subject)
        if parsed:
            result.rights_ratio_from, result.rights_ratio_to, result.rights_price = parsed
        else:
            result.parse_status = "PARTIAL"
            result.parse_notes = f"Classified as RIGHTS but could not parse details from: '{subject}'"

    elif action_type == "UNKNOWN":
        result.parse_status = "FAILED"
        result.parse_notes = f"Could not classify action from: '{subject}'"

    return result


def _parse_nse_date(date_str: str) -> Optional[date]:
    """Parse NSE date formats: 'DD-Mon-YYYY', 'DD-MM-YYYY', 'YYYY-MM-DD'."""
    if not date_str or date_str.strip() == "-" or date_str.strip() == "":
        return None

    date_str = date_str.strip()

    formats = [
        "%d-%b-%Y",  # 25-Sep-2026
        "%d-%m-%Y",  # 25-09-2026
        "%d %b %Y",  # 25 Sep 2026
        "%Y-%m-%d",  # 2026-09-25
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    return None


# ======================================================================
# Validation
# ======================================================================

def validate_corporate_action(action: ParsedCorporateAction) -> List[str]:
    """Validate a parsed corporate action. Returns list of issues (empty = valid)."""
    issues = []

    if not action.symbol:
        issues.append("Missing symbol")

    if action.ex_date == date(1900, 1, 1):
        issues.append("Invalid ex_date")

    if action.action_type == "UNKNOWN":
        issues.append(f"Unknown action type: '{action.raw_subject}'")

    if action.action_type == "SPLIT":
        if not action.ratio_from or not action.ratio_to:
            issues.append("SPLIT missing ratio")
        elif action.ratio_to <= action.ratio_from:
            issues.append(f"SPLIT ratio_to ({action.ratio_to}) should be > ratio_from ({action.ratio_from})")

    if action.action_type == "BONUS":
        if not action.ratio_from or not action.ratio_to:
            issues.append("BONUS missing ratio")

    if action.action_type == "DIVIDEND":
        if not action.dividend_amount or action.dividend_amount <= 0:
            issues.append("DIVIDEND missing or invalid amount")

    if action.action_type == "RIGHTS":
        if not action.rights_price or action.rights_price <= 0:
            issues.append("RIGHTS missing or invalid price")

    return issues


# ======================================================================
# NSE Corporate Action Source (download + parse)
# ======================================================================

class NSECorporateActionSource:
    """Adapter for downloading and parsing NSE corporate actions.

    Uses the same session bootstrap as NSEDataSource (homepage-first for cookies).
    """

    # NSE corporate actions page
    CA_URL = "https://www.nseindia.com/api/corporates-corporateActions"
    HOME_URL = "https://www.nseindia.com"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-corporate-actions",
    }

    def __init__(self, raw_dir: Path):
        self.raw_dir = Path(raw_dir)
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        """Bootstrap session with NSE homepage cookies."""
        if self._session is None:
            s = requests.Session()
            s.headers.update(self.HEADERS)
            resp = s.get(self.HOME_URL, timeout=15)
            resp.raise_for_status()
            self._session = s
        return self._session

    def download_actions(
        self,
        start_date: date,
        end_date: date,
        index: str = "equities",
    ) -> List[dict]:
        """Download corporate actions from NSE for a date range.

        Args:
            start_date: Start of the range (inclusive).
            end_date: End of the range (inclusive).
            index: 'equities' (default) or specific index.

        Returns:
            List of raw action dicts from NSE.
        """
        session = self._get_session()

        params = {
            "index": index,
            "from_date": start_date.strftime("%d-%m-%Y"),
            "to_date": end_date.strftime("%d-%m-%Y"),
        }

        resp = session.get(self.CA_URL, params=params, timeout=30)

        if resp.status_code == 403:
            # Re-bootstrap session
            self._session = None
            session = self._get_session()
            resp = session.get(self.CA_URL, params=params, timeout=30)

        resp.raise_for_status()

        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise CorporateActionParseError(
                f"NSE returned non-JSON response for corporate actions. "
                f"Status: {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}"
            )

        # Store raw response for audit
        self._store_raw(data, start_date, end_date)

        return data if isinstance(data, list) else []

    def _store_raw(self, data: object, start_date: date, end_date: date) -> Path:
        """Store raw response for audit trail."""
        out_dir = self.raw_dir / "NSE_CA"
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"ca_{start_date:%Y%m%d}_{end_date:%Y%m%d}.json"
        out_path = out_dir / filename

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("Stored raw CA response: %s", out_path)
        return out_path

    def fetch_and_parse(
        self,
        start_date: date,
        end_date: date,
    ) -> Tuple[List[ParsedCorporateAction], dict]:
        """Download, parse, validate, and return normalized corporate actions.

        Returns:
            (actions, report) where:
                actions: List of validated ParsedCorporateAction objects
                report: dict with parsing statistics
        """
        raw_actions = self.download_actions(start_date, end_date)

        parsed = []
        failed = []
        partial = []

        for raw in raw_actions:
            action = parse_nse_corporate_action(raw)
            issues = validate_corporate_action(action)

            if issues:
                action.parse_status = "FAILED"
                action.parse_notes = "; ".join(issues)
                failed.append(action)
            elif action.parse_status == "PARTIAL":
                partial.append(action)
            else:
                parsed.append(action)

        report = {
            "date_range": (str(start_date), str(end_date)),
            "total_raw": len(raw_actions),
            "parsed_ok": len(parsed),
            "parsed_partial": len(partial),
            "parse_failed": len(failed),
            "action_types": {},
        }

        for a in parsed:
            report["action_types"][a.action_type] = report["action_types"].get(a.action_type, 0) + 1

        logger.info(
            "CA parse report: %d raw → %d OK + %d partial + %d failed",
            len(raw_actions), len(parsed), len(partial), len(failed),
        )

        return parsed + partial, report


# ======================================================================
# Database insertion (idempotent)
# ======================================================================

def insert_corporate_actions(
    engine,
    actions: List[ParsedCorporateAction],
) -> dict:
    """Insert parsed corporate actions into the database.

    Idempotent: uses ON CONFLICT (security_id, action_type, ex_date)
    to skip duplicates.

    Returns insert report dict.
    """
    from sqlalchemy import text

    inserted = 0
    skipped = 0
    errors = []

    with engine.begin() as conn:
        for action in actions:
            if action.parse_status == "FAILED":
                skipped += 1
                continue

            # Look up security_id
            sec_row = conn.execute(
                text("""
                    SELECT id FROM securities
                    WHERE symbol = :symbol AND series = :series
                    LIMIT 1
                """),
                {"symbol": action.symbol, "series": action.series},
            ).fetchone()

            if sec_row is None:
                errors.append(f"Security not found: {action.symbol}/{action.series}")
                skipped += 1
                continue

            security_id = sec_row[0]

            try:
                conn.execute(
                    text("""
                        INSERT INTO corporate_actions (
                            security_id, action_type, ex_date, record_date,
                            ratio_from, ratio_to,
                            dividend_amount, dividend_type,
                            rights_price, rights_ratio_from, rights_ratio_to,
                            source, raw_data, event_version
                        ) VALUES (
                            :security_id, :action_type, :ex_date, :record_date,
                            :ratio_from, :ratio_to,
                            :dividend_amount, :dividend_type,
                            :rights_price, :rights_ratio_from, :rights_ratio_to,
                            :source, :raw_data, :event_version
                        )
                        ON CONFLICT (security_id, action_type, ex_date) DO NOTHING
                    """),
                    {
                        "security_id": security_id,
                        "action_type": action.action_type,
                        "ex_date": action.ex_date,
                        "record_date": action.record_date,
                        "ratio_from": action.ratio_from,
                        "ratio_to": action.ratio_to,
                        "dividend_amount": action.dividend_amount,
                        "dividend_type": action.dividend_type,
                        "rights_price": action.rights_price,
                        "rights_ratio_from": action.rights_ratio_from,
                        "rights_ratio_to": action.rights_ratio_to,
                        "source": action.source,
                        "raw_data": json.dumps({
                            "raw_subject": action.raw_subject,
                            "parse_status": action.parse_status,
                            "parse_notes": action.parse_notes,
                        }),
                        "event_version": action.event_version,
                    },
                )
                inserted += 1
            except Exception as e:
                errors.append(f"{action.symbol} {action.action_type} {action.ex_date}: {e}")
                skipped += 1

    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }
