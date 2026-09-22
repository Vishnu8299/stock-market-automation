# stock-market-automation — M0

Research-first foundation: get NSE historical data in, clean, and
queryable before anything touches a broker. See `docs/research-log.md`
for what's confirmed vs. assumed.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Postgres + TimescaleDB (adjust to your local install)
psql -d market_research -f schema.sql

export PGHOST=localhost PGDATABASE=market_research PGUSER=postgres PGPASSWORD=yourpassword
```

## Run

```bash
# Full pipeline: download, parse, validate, insert into Postgres
python ingest.py --date 2026-09-19

# Dry run — skip the DB write (useful before Postgres is set up)
python ingest.py --date 2026-09-19 --skip-db
```

Output matches the M0.1 Definition of Done:

```
========================================
NSE INGESTION REPORT
========================================

Date:              2026-09-19
Source:            NSE_CM_UDIFF
Downloaded:        YES
File hash:         <sha256>

Rows received:     XXXXX
Rows accepted:     XXXXX
Rows rejected:     X

Duplicates:        0
OHLC violations:   0

Database insert:   SUCCESS

Status:             PASS
========================================
```

Re-running for the same date is safe — the raw file is never
overwritten, and DB inserts are idempotent.

## Tests

```bash
pytest tests/ -v
```

Verifies parse → normalize → validate against a synthetic bhavcopy with
a deliberately injected duplicate, OHLC violation, and missing field —
confirms the integrity checks actually catch bad data.

## Project layout

```
ingestion/
    nse_source.py   — download/parse/normalize (swap this if NSE changes format again)
    validators.py   — duplicate / OHLC / missing / negative checks
    db.py           — PostgreSQL + TimescaleDB inserts
    report.py       — ingestion report
ingest.py            — CLI entrypoint
schema.sql           — DB schema
tests/                — pytest suite
docs/research-log.md — what's confirmed vs. assumed, open items
```

## Known open items (see docs/research-log.md)

- Live download against NSE not yet verified from a network with access
  to nseindia.com (this build ran in a sandboxed environment without it).
- No Postgres instance was available to test `db.py` end-to-end.
- Column mapping in `normalize()` is built from NSE's UDiFF documentation,
  not a live sample file — worth a spot-check on the first real run.
