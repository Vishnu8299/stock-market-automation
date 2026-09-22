#!/usr/bin/env python3
"""
M0.1 — NSE Data Ingestion Proof of Concept.

Usage:
    python ingest.py --date YYYY-MM-DD [--raw-dir data/raw] [--skip-db]

Pipeline:
    download -> parse -> normalize -> validate -> insert -> report

--skip-db runs everything except the database write, so the pipeline can
be exercised without a Postgres/TimescaleDB instance on hand (useful for
first-run testing on a laptop before the DB is set up).
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from ingestion.nse_source import NSEDataSource, NSEDownloadError
from ingestion.validators import validate
from ingestion.report import IngestionReport
from ingestion import db as dbmod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest one day of NSE bhavcopy data.")
    parser.add_argument("--date", required=True, help="Trading date, YYYY-MM-DD")
    parser.add_argument("--raw-dir", default="data/raw", help="Directory for immutable raw files")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Run download/parse/normalize/validate only — skip the DB write.",
    )
    args = parser.parse_args()

    try:
        trading_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        logger.error("Invalid --date, expected YYYY-MM-DD")
        return 1

    source = NSEDataSource(raw_dir=Path(args.raw_dir))

    try:
        raw_file = source.download(trading_date)
        raw_df = source.parse(raw_file)
        norm_df = source.normalize(raw_df, trading_date)
    except NSEDownloadError as e:
        logger.error(str(e))
        print(
            IngestionReport(
                trading_date=trading_date,
                source="NSE_CM_UDIFF",
                downloaded=False,
                file_hash="-",
                rows_received=0,
                rows_accepted=0,
                rows_rejected=0,
                duplicates=0,
                ohlc_violations=0,
                db_insert_success=False,
                status="FAIL",
            ).render()
        )
        return 1

    result = validate(norm_df)
    for err in result.errors:
        logger.warning(err)

    db_insert_success = False
    if not args.skip_db:
        try:
            engine = dbmod.get_engine()
            dbmod.upsert_securities(engine, result.clean_df)
            dbmod.insert_daily_prices(engine, result.clean_df)
            dbmod.log_ingestion_run(
                engine,
                source="NSE_CM_UDIFF",
                trading_date=trading_date,
                file_hash=raw_file.sha256,
                row_count=result.rows_accepted,
                status="PASS" if result.rows_rejected == 0 else "PARTIAL",
            )
            db_insert_success = True
        except Exception as e:
            logger.error("Database insert failed: %s", e)
    else:
        logger.info(
            "--skip-db set: parsed and validated %d rows, no DB write performed",
            result.rows_accepted,
        )

    status = "PASS" if (result.rows_rejected == 0 and (db_insert_success or args.skip_db)) else "PARTIAL"

    report = IngestionReport(
        trading_date=trading_date,
        source="NSE_CM_UDIFF",
        downloaded=True,
        file_hash=raw_file.sha256,
        rows_received=result.rows_received,
        rows_accepted=result.rows_accepted,
        rows_rejected=result.rows_rejected,
        duplicates=result.duplicates,
        ohlc_violations=result.ohlc_violations,
        db_insert_success=db_insert_success,
        status=status,
    )
    print(report.render())
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
