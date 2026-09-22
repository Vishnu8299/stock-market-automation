from dataclasses import dataclass
from datetime import date


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
            f"Rows received:     {self.rows_received}",
            f"Rows accepted:     {self.rows_accepted}",
            f"Rows rejected:     {self.rows_rejected}",
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
