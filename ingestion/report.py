from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class IngestionReport:
    trading_date: date
    source: str
    downloaded: bool
    file_hash: str
    rows_received: int
    rows_accepted: int
    rows_rejected: int
    duplicates: int
    ohlc_violations: int
    db_insert_success: bool
    status: str
    rows_inserted: int = 0
    rows_skipped: int = 0
    # Provenance metadata — added per Team Lead M0.1.1 requirement
    source_file: str = ""
    source_timestamp: str = ""
    parser_version: str = "1.0.0"
    schema_version: str = "M0.1"
    dataset_version: str = "NSE_CM_UDiFF_2024"

    def render(self) -> str:
        lines = [
            "=" * 40,
            "NSE INGESTION REPORT",
            "=" * 40,
            "",
            f"Date:              {self.trading_date}",
            f"Source:            {self.source}",
            f"Downloaded:        {'YES' if self.downloaded else 'NO'}",
            f"File hash:         {self.file_hash}",
            "",
            "--- Source Integrity ---",
            f"Source file:       {self.source_file or 'N/A'}",
            f"SHA-256:           {self.file_hash}",
            f"Source timestamp:  {self.source_timestamp or 'N/A'}",
            f"Parser version:    {self.parser_version}",
            f"Schema version:    {self.schema_version}",
            f"Dataset version:   {self.dataset_version}",
            "",
            f"Rows received:     {self.rows_received}",
            f"Rows accepted:     {self.rows_accepted}",
            f"Rows rejected:     {self.rows_rejected}",
            "",
            f"Rows inserted:     {self.rows_inserted}",
            f"Rows skipped:      {self.rows_skipped}",
            "",
            f"Duplicates:        {self.duplicates}",
            f"OHLC violations:   {self.ohlc_violations}",
            "",
            f"Database insert:   {'SUCCESS' if self.db_insert_success else 'SKIPPED/FAILED'}",
            "",
            f"Status:             {self.status}",
            "=" * 40,
        ]
        return "\n".join(lines)

