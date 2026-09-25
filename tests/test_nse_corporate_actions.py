"""
NSE Corporate Action Ingestion Tests — real NSE subject-line parsing.

Tests the parser against actual NSE corporate action subject lines
that appear in their API/website. This converts the ASSUMPTION:

    "NSE corporate action data can be parsed into CorporateAction objects"

into a FACT through regression tests.

Each test uses a real subject-line format observed from NSE data.
"""

import pytest
from datetime import date

from ingestion.nse_corporate_actions import (
    parse_split_subject,
    parse_bonus_subject,
    parse_dividend_subject,
    parse_rights_subject,
    classify_action,
    parse_nse_corporate_action,
    validate_corporate_action,
    ParsedCorporateAction,
    _parse_nse_date,
)


# ======================================================================
# 1. SPLIT PARSING — real NSE subject lines
# ======================================================================

class TestSplitParsing:
    """Verify split parsing against real NSE subject formats."""

    def test_face_value_10_to_2(self):
        """'Stock Split From Rs.10/- to Rs.2/-' → 1:5"""
        result = parse_split_subject("Stock Split From Rs.10/- to Rs.2/-")
        assert result == (1, 5), f"Expected (1,5), got {result}"

    def test_face_value_10_to_5(self):
        """'Stock Split From Rs.10/- to Rs.5/-' → 1:2"""
        result = parse_split_subject("Stock Split From Rs.10/- to Rs.5/-")
        assert result == (1, 2), f"Expected (1,2), got {result}"

    def test_face_value_10_to_1(self):
        """'Stock Split From Rs.10/- to Rs.1/-' → 1:10"""
        result = parse_split_subject("Stock Split From Rs.10/- to Rs.1/-")
        assert result == (1, 10), f"Expected (1,10), got {result}"

    def test_no_dash_format(self):
        """'Stock Split From Rs 10 to Rs 2' → 1:5"""
        result = parse_split_subject("Stock Split From Rs 10 to Rs 2")
        assert result == (1, 5)

    def test_dot_format(self):
        """'Face Value Split Rs. 10 to Rs. 2' → 1:5"""
        result = parse_split_subject("Face Value Split Rs. 10 to Rs. 2")
        assert result == (1, 5)

    def test_face_value_5_to_1(self):
        """'Stock Split From Rs.5/- to Rs.1/-' → 1:5"""
        result = parse_split_subject("Stock Split From Rs.5/- to Rs.1/-")
        assert result == (1, 5)

    def test_split_ratio_format(self):
        """'Stock Split 1:5' → 1:5"""
        result = parse_split_subject("Stock Split 1:5")
        assert result == (1, 5)

    def test_unparseable_returns_none(self):
        """Garbage text should return None, not crash."""
        result = parse_split_subject("Some random text about splitting hairs")
        assert result is None


# ======================================================================
# 2. BONUS PARSING — real NSE subject lines
# ======================================================================

class TestBonusParsing:
    """Verify bonus parsing against real NSE subject formats."""

    def test_bonus_1_to_1(self):
        """'Bonus issue 1:1' → (1, 1)"""
        result = parse_bonus_subject("Bonus issue 1:1")
        assert result == (1, 1)

    def test_bonus_2_to_1(self):
        """'Bonus 2:1' → (2, 1)"""
        result = parse_bonus_subject("Bonus 2:1")
        assert result == (2, 1)

    def test_bonus_1_to_2(self):
        """'Bonus Issue 1:2' → (1, 2)"""
        result = parse_bonus_subject("Bonus Issue 1:2")
        assert result == (1, 2)

    def test_bonus_3_to_1(self):
        """'Bonus 3:1' → (3, 1)"""
        result = parse_bonus_subject("Bonus 3:1")
        assert result == (3, 1)

    def test_bonus_with_slash(self):
        """'Bonus Issue 1/1' → (1, 1)"""
        result = parse_bonus_subject("Bonus Issue 1/1")
        assert result == (1, 1)

    def test_unparseable_returns_none(self):
        result = parse_bonus_subject("Some bonus information without ratio")
        assert result is None


# ======================================================================
# 3. DIVIDEND PARSING — real NSE subject lines
# ======================================================================

class TestDividendParsing:
    """Verify dividend parsing against real NSE subject formats."""

    def test_simple_dividend(self):
        """'Dividend - Rs 20 Per Share' → (20.0, 'REGULAR')"""
        result = parse_dividend_subject("Dividend - Rs 20 Per Share")
        assert result == (20.0, "REGULAR")

    def test_final_dividend(self):
        """'Final Dividend - Rs.18.50 Per Share' → (18.50, 'FINAL')"""
        result = parse_dividend_subject("Final Dividend - Rs.18.50 Per Share")
        assert result == (18.50, "FINAL")

    def test_interim_dividend(self):
        """'Interim Dividend - Rs 5 Per Share' → (5.0, 'INTERIM')"""
        result = parse_dividend_subject("Interim Dividend - Rs 5 Per Share")
        assert result == (5.0, "INTERIM")

    def test_special_dividend(self):
        """'Special Dividend Rs.100/-' → (100.0, 'SPECIAL')"""
        result = parse_dividend_subject("Special Dividend Rs.100/-")
        assert result == (100.0, "SPECIAL")

    def test_dividend_with_slash_dash(self):
        """'Dividend - Rs 7.50/- Per Share' → (7.50, 'REGULAR')"""
        result = parse_dividend_subject("Dividend - Rs 7.50/- Per Share")
        assert result == (7.50, "REGULAR")

    def test_dividend_decimal(self):
        """'Dividend Rs.3.50 per share' → (3.50, 'REGULAR')"""
        result = parse_dividend_subject("Dividend Rs.3.50 per share")
        assert result == (3.50, "REGULAR")

    def test_unparseable_returns_none(self):
        result = parse_dividend_subject("Some dividend information")
        assert result is None


# ======================================================================
# 4. RIGHTS PARSING — real NSE subject lines
# ======================================================================

class TestRightsParsing:
    """Verify rights parsing against real NSE subject formats."""

    def test_rights_1_5_at_1000(self):
        """'Rights Issue 1:5 @ Rs 1000 Per Share' → (5, 1, 1000.0)"""
        result = parse_rights_subject("Rights Issue 1:5 @ Rs 1000 Per Share")
        assert result == (5, 1, 1000.0)

    def test_rights_2_7_at_250(self):
        """'Rights 2:7 @ Rs.250' → (7, 2, 250.0)"""
        result = parse_rights_subject("Rights 2:7 @ Rs.250")
        assert result == (7, 2, 250.0)

    def test_unparseable_returns_none(self):
        result = parse_rights_subject("Rights announcement")
        assert result is None


# ======================================================================
# 5. ACTION CLASSIFIER
# ======================================================================

class TestActionClassifier:
    """Verify subject-line classification."""

    def test_split_classification(self):
        assert classify_action("Stock Split From Rs.10/- to Rs.2/-") == "SPLIT"

    def test_bonus_classification(self):
        assert classify_action("Bonus issue 1:1") == "BONUS"

    def test_dividend_classification(self):
        assert classify_action("Final Dividend - Rs.18.50 Per Share") == "DIVIDEND"

    def test_interim_dividend_classification(self):
        assert classify_action("Interim Dividend - Rs 5 Per Share") == "DIVIDEND"

    def test_rights_classification(self):
        assert classify_action("Rights Issue 1:5 @ Rs 1000") == "RIGHTS"

    def test_unknown_classification(self):
        assert classify_action("AGM/EGM") == "UNKNOWN"


# ======================================================================
# 6. DATE PARSING — real NSE date formats
# ======================================================================

class TestDateParsing:
    """Verify NSE date format handling."""

    def test_dd_mon_yyyy(self):
        assert _parse_nse_date("25-Sep-2026") == date(2026, 9, 25)

    def test_dd_mm_yyyy(self):
        assert _parse_nse_date("25-09-2026") == date(2026, 9, 25)

    def test_yyyy_mm_dd(self):
        assert _parse_nse_date("2026-09-25") == date(2026, 9, 25)

    def test_dd_mon_yyyy_space(self):
        assert _parse_nse_date("25 Sep 2026") == date(2026, 9, 25)

    def test_dash_returns_none(self):
        assert _parse_nse_date("-") is None

    def test_empty_returns_none(self):
        assert _parse_nse_date("") is None

    def test_garbage_returns_none(self):
        assert _parse_nse_date("not-a-date") is None


# ======================================================================
# 7. END-TO-END PARSE — simulated NSE response
# ======================================================================

class TestEndToEndParse:
    """Simulate real NSE API responses and verify complete parsing."""

    def test_split_end_to_end(self):
        """Simulate a real NSE split record."""
        raw = {
            "symbol": "RELIANCE",
            "series": "EQ",
            "subject": "Stock Split From Rs.10/- to Rs.2/-",
            "exDt": "25-Sep-2026",
            "recDt": "26-Sep-2026",
        }

        action = parse_nse_corporate_action(raw)

        assert action.symbol == "RELIANCE"
        assert action.series == "EQ"
        assert action.action_type == "SPLIT"
        assert action.ex_date == date(2026, 9, 25)
        assert action.ratio_from == 1
        assert action.ratio_to == 5
        assert action.parse_status == "OK"

        issues = validate_corporate_action(action)
        assert len(issues) == 0, f"Validation issues: {issues}"

    def test_bonus_end_to_end(self):
        """Simulate a real NSE bonus record."""
        raw = {
            "symbol": "TCS",
            "series": "EQ",
            "subject": "Bonus issue 1:1",
            "exDt": "15-Jun-2026",
            "recDt": "16-Jun-2026",
        }

        action = parse_nse_corporate_action(raw)

        assert action.action_type == "BONUS"
        assert action.ratio_from == 1
        assert action.ratio_to == 1
        assert action.parse_status == "OK"

    def test_dividend_end_to_end(self):
        """Simulate a real NSE dividend record."""
        raw = {
            "symbol": "INFY",
            "series": "EQ",
            "subject": "Final Dividend - Rs.18.50 Per Share",
            "exDt": "01-Jul-2026",
            "recDt": "02-Jul-2026",
        }

        action = parse_nse_corporate_action(raw)

        assert action.action_type == "DIVIDEND"
        assert action.dividend_amount == 18.50
        assert action.dividend_type == "FINAL"
        assert action.parse_status == "OK"

    def test_unknown_action_fails_validation(self):
        """Actions we can't classify should fail validation."""
        raw = {
            "symbol": "TEST",
            "series": "EQ",
            "subject": "Annual General Meeting",
            "exDt": "15-Sep-2026",
        }

        action = parse_nse_corporate_action(raw)
        issues = validate_corporate_action(action)

        assert action.action_type == "UNKNOWN"
        assert len(issues) > 0

    def test_missing_date_fails(self):
        """Missing ex_date should fail."""
        raw = {
            "symbol": "TEST",
            "series": "EQ",
            "subject": "Bonus 1:1",
            "exDt": "",
        }

        action = parse_nse_corporate_action(raw)
        assert action.parse_status == "FAILED"

    def test_provenance_preserved(self):
        """Raw subject must be preserved in parsed action."""
        subject = "Stock Split From Rs.10/- to Rs.2/-"
        raw = {
            "symbol": "ABC",
            "series": "EQ",
            "subject": subject,
            "exDt": "01-Jan-2026",
        }

        action = parse_nse_corporate_action(raw)
        assert action.raw_subject == subject
        assert action.source == "NSE_CA"
        assert action.event_version == "1.0"

    def test_purpose_field_fallback(self):
        """Some NSE responses use 'purpose' instead of 'subject'."""
        raw = {
            "symbol": "HDFC",
            "series": "EQ",
            "purpose": "Interim Dividend - Rs 5 Per Share",
            "exDt": "15-Mar-2026",
        }

        action = parse_nse_corporate_action(raw)
        assert action.action_type == "DIVIDEND"
        assert action.dividend_amount == 5.0
        assert action.dividend_type == "INTERIM"


# ======================================================================
# 8. VALIDATION RULES
# ======================================================================

class TestValidationRules:
    """Verify validation catches bad data."""

    def test_split_ratio_to_must_exceed_from(self):
        """A split where ratio_to <= ratio_from is invalid."""
        action = ParsedCorporateAction(
            symbol="TEST", series="EQ", action_type="SPLIT",
            ex_date=date(2026, 1, 1), record_date=None,
            ratio_from=5, ratio_to=1,  # Wrong — this is a reverse split
        )
        issues = validate_corporate_action(action)
        assert any("ratio_to" in i for i in issues)

    def test_dividend_zero_amount_invalid(self):
        """Zero dividend is invalid."""
        action = ParsedCorporateAction(
            symbol="TEST", series="EQ", action_type="DIVIDEND",
            ex_date=date(2026, 1, 1), record_date=None,
            dividend_amount=0.0,
        )
        issues = validate_corporate_action(action)
        assert any("amount" in i.lower() for i in issues)

    def test_missing_symbol_invalid(self):
        """Empty symbol is invalid."""
        action = ParsedCorporateAction(
            symbol="", series="EQ", action_type="SPLIT",
            ex_date=date(2026, 1, 1), record_date=None,
            ratio_from=1, ratio_to=5,
        )
        issues = validate_corporate_action(action)
        assert any("symbol" in i.lower() for i in issues)

    def test_valid_action_passes(self):
        """A correctly formed action should have zero issues."""
        action = ParsedCorporateAction(
            symbol="RELIANCE", series="EQ", action_type="SPLIT",
            ex_date=date(2026, 9, 25), record_date=date(2026, 9, 26),
            ratio_from=1, ratio_to=5,
        )
        issues = validate_corporate_action(action)
        assert len(issues) == 0
