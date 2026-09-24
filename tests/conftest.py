"""
Shared pytest fixtures for M0 test suite.

Provides:
- db_engine: SQLAlchemy engine from .env / environment variables (skips if unavailable)
- clean_db: Truncates test-relevant tables before each DB test
- sample_data_2026_09_18: Loads, parses, normalizes, validates the real 2026-09-18 bhavcopy
"""

import os
import sys
from datetime import date
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _db_available() -> bool:
    """Check if PostgreSQL is reachable without crashing pytest collection."""
    try:
        from ingestion.db import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# Evaluated once at collection time
_DB_OK = _db_available()


@pytest.fixture(scope="session")
def db_engine():
    """SQLAlchemy engine connected to the local dev database.

    Skips the entire test if PostgreSQL is not available.
    """
    if not _DB_OK:
        pytest.skip("PostgreSQL not available (check .env / PGHOST, PGPASSWORD)")
    from ingestion.db import get_engine
    return get_engine()


@pytest.fixture
def clean_db(db_engine):
    """Truncate daily_prices, securities, and data_ingestion_runs before the test.

    Uses TRUNCATE ... CASCADE so foreign-key order doesn't matter.
    """
    from sqlalchemy import text
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE daily_prices, securities, data_ingestion_runs CASCADE"))
    yield db_engine


@pytest.fixture(scope="session")
def sample_data_2026_09_18():
    """Load and validate the real 2026-09-18 NSE bhavcopy.

    Returns (raw_file, validated_result) where validated_result is a
    ValidationResult with .clean_df ready for insertion.

    Skips if the raw file isn't present (hasn't been downloaded yet).
    """
    from ingestion.nse_source import NSEDataSource, RawFile
    from ingestion.validators import validate

    raw_dir = Path("data/raw")
    raw_path = raw_dir / "NSE" / "2026" / "09" / "18" / "original_file.zip"

    if not raw_path.exists():
        pytest.skip(
            f"Raw bhavcopy not found at {raw_path}. "
            f"Run `python ingest.py --date 2026-09-18 --skip-db` first."
        )

    source = NSEDataSource(raw_dir=raw_dir)
    raw_file = source._describe(raw_path)
    raw_df = source.parse(raw_file)
    trading_date = date(2026, 9, 18)
    norm_df = source.normalize(raw_df, trading_date)
    result = validate(norm_df)

    return raw_file, result
