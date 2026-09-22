# Research Log

## M0.1 — NSE Data Ingestion PoC (Vishnu)

**Status:** PASS — build complete, tests pass, logic verified against a
synthetic bhavcopy. **Not yet verified against a live NSE download or a
real Postgres/TimescaleDB instance** — this build environment has no
network access to nseindia.com and no Postgres server available, so
those two steps still need to run on a real machine before this is
truly "done."

### What was built
- `ingestion/nse_source.py` — adapter: `download()`, `parse()`, `normalize()`
- `ingestion/validators.py` — duplicate / OHLC / missing / negative-value checks
- `ingestion/db.py` — PostgreSQL + TimescaleDB insert layer (idempotent upserts)
- `ingestion/report.py` — ingestion report, matches the M0.1 Definition of Done format
- `ingest.py` — CLI: `python ingest.py --date YYYY-MM-DD [--skip-db]`
- `schema.sql` — DDL for `securities`, `daily_prices` (hypertable), `corporate_actions`, `data_ingestion_runs`
- `tests/test_ingestion.py` — synthetic bhavcopy with an injected duplicate,
  OHLC violation, and missing field; confirms the validators catch exactly
  what they're supposed to

### Claims, labeled

- **FACT** — NSE's public CM bhavcopy URL pattern changed on 2024-07-08 to
  `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip`
  (confirmed via NSE's UDiFF documentation and community reports of the
  2024 migration).
- **ASSUMPTION** — this public URL is unaffected by NSE's 2026-10-12
  Extranet dissemination change (circular NSE/MSD/74764), which as
  published targets member-only Extranet `.DAT` paths, not the public
  website report. **Unverified** — could not reach nseindia.com from
  this build environment to confirm. First thing to check if ingestion
  starts failing on/after that date.
- **ASSUMPTION** — NSE requires a session cookie from the homepage before
  the bhavcopy endpoint will respond (returns 403 otherwise). Built into
  `_get_session()`. Based on common NSE-scraping reports, not directly
  verified from this environment.
- **HYPOTHESIS** — none yet; M0.1 is pure data engineering, no trading
  hypotheses in scope.

### What still needs a real machine (not this sandbox)
1. Run `python ingest.py --date <last trading day> --skip-db` against
   live NSE to confirm `download()` actually gets past NSE's bot
   defenses (User-Agent + cookie may not be enough — some sites also
   need TLS/JA3 fingerprint matching that `requests` can't fake; if it
   fails, next step is trying `curl_cffi` or a headless browser instead).
2. Run `schema.sql` against a real PostgreSQL + TimescaleDB instance,
   then run `ingest.py` without `--skip-db` to confirm the insert path.
3. Confirm UDiFF column names in a real downloaded file match exactly
   what's hard-coded in `normalize()` — built from documentation, not a
   live sample.

### Next candidate milestone
Once live download + DB insert are confirmed: extend `ingest.py` to
backfill a date range (not just one day), and add the trading-calendar
table so "missing session" can be distinguished from "ingestion failure."
