# Research Log

## M0.1 — NSE Data Ingestion Foundation (Vishnu)

**Status: CLOSED ✅** — 2026-09-24

### Frozen Dataset Provenance

```
Dataset version:  M0.1
Parser version:   1.0.0
Schema version:   M0.1
Dataset date:     2026-09-18
Source rows:      3,660
Validated rows:   3,660
Inserted rows:    3,660
Dataset hash:     cb157bc6b30f2c4a12335a650381280bdad8d199edff541cba6cbd5e205d3320
```

**This is a reproducibility anchor.** Do not modify ingestion behavior
without creating a new version and recording it.

### What was built
- `ingestion/nse_source.py` — adapter: `download()`, `parse()`, `normalize()`
- `ingestion/validators.py` — duplicate / OHLC / missing / negative-value checks
- `ingestion/db.py` — PostgreSQL insert layer (idempotent upserts, InsertStats)
- `ingestion/report.py` — ingestion report, matches the M0.1 Definition of Done format
- `ingestion/data_interface.py` — standardized market data API for backtester
- `ingest.py` — CLI: `python ingest.py --date YYYY-MM-DD [--skip-db]`
- `schema.sql` — DDL (TimescaleDB optional, degrades gracefully to plain PostgreSQL)
- `tests/test_ingestion.py` — synthetic tests + ENTERO series + NaN-series regression
- `tests/test_nse_session.py` — NSE 403 bootstrap behavior regression
- `tests/test_db_validation.py` — C1 Insert, C2 Round-trip, C3 Idempotency
- `tests/run_test_c.py` — M0.1 closure report script

### Three bugs discovered during live validation

These demonstrate why M0.1 required live-data + live-DB testing, not just
synthetic tests.

**Bug 1: NSE 403 session bootstrap** (discovered during Test A)
- NSE returns HTTP 403 to requests without a session cookie from the homepage.
- The original code assumed a simple GET would work.
- Fix: `_get_session()` hits the homepage first, then reuses the session.
- Regression test: `tests/test_nse_session.py`

**Bug 2: Dedup key missing `series`** (discovered during Test B)
- NSE lists the same symbol in multiple series (e.g. ENTERO in BL and EQ).
- The original dedup key was `(symbol, trading_date)`, which silently discarded
  legitimate records in different series.
- Fix: dedup key corrected to `(symbol, series, trading_date)` in both
  the validator and the database schema.
- Regression test: `test_entero_different_series_both_valid`,
  `test_entero_same_series_is_duplicate`

**Bug 3: NaN series for debt instruments** (discovered during Test C)
- 7 NSE debt instruments (bonds, NCDs) have blank/missing series in the
  source CSV. Python's `float('nan') != float('nan')` caused a lookup
  mismatch between `upsert_securities()` and `insert_daily_prices()`,
  silently dropping 7 records.
- Fix: `normalize()` fills NaN series with `""` via `fillna("").astype(str).str.strip()`
- Regression test: `test_nan_series_normalized_to_empty_string`,
  `test_nan_series_dedup_works_correctly`

### Claims, labeled

- **FACT** — NSE's public CM bhavcopy URL pattern:
  `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip`
  Successfully used to download data on 2026-09-18, 2026-09-19.
- **FACT** — NSE requires a session cookie from the homepage before the
  bhavcopy endpoint will respond. Confirmed during live validation.
- **FACT** — `requests` (plain HTTP client) with browser-like headers is
  sufficient. No headless browser needed as of 2026-09-24.
- **ASSUMPTION** — the public URL is unaffected by NSE's 2026-10-12
  Extranet dissemination change. Monitor after that date.
- **HYPOTHESIS** — none; M0.1 is pure data engineering.

### Architectural decisions (locked)

- Security identity: `symbol + series + trading_date`
- Security master system: deferred (see `docs/architecture-backlog.md`)
- Data layer abstraction: `DataInterface.load_market_data()` — backtester
  must not know or care about data source
- No coupling rule: `backtest.py → load_market_data()`, never `download_nse_data()`

---

## M0.2 — Backtesting Foundation (Bharath + Vishnu)

**Status: INTEGRATION** — started 2026-09-24

### Pre-experiment requirements (Team Lead)

1. **Corporate-action adjustment: NOT YET IMPLEMENTED**
   - Raw data acceptable for software test, not for deployment evidence.
2. **Indian statutory cost model: NOT YET IMPLEMENTED**
   - Simplified cost model for integration test only.
3. **Benchmark normalization required**
   - Buy & Hold must use identical starting capital, entry assumptions,
     cost treatment, corporate-action treatment, and data period.

### M0.2-I1 — Integration acceptance test

Before any full historical experiment:
```
Known NSE dataset → Known security → DataInterface → Backtester
→ Expected first/last dates → Expected observations → No duplicates
→ No invalid OHLC → PASS
```

### Next candidate milestone
Once M0.2-I1 passes: run the first proper NSE-based 50/200 SMA experiment
using the validated dataset through the DataInterface.

