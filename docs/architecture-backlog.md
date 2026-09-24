# Architecture Backlog

Decisions locked by Team Lead (Vishnu) during M0.1.
These are **not** to be implemented until explicitly scheduled, but must be respected
in all current design choices.

---

## 1. Security Identity — Locked Decision

> **`security identity = symbol + series + trading_date` for the daily price record.**

This was confirmed during M0.1 live validation when we discovered that NSE lists
the same symbol in multiple series (e.g. ENTERO in both BL and EQ on the same date).
The dedup key in both the validator and the database schema was corrected from
`(symbol, trading_date)` to `(symbol, series, trading_date)`.

**Current schema enforcement:**
- `securities` table: `UNIQUE (symbol, series, exchange)`
- `daily_prices` table: `PRIMARY KEY (security_id, trading_date)`
- Validator dedup: `duplicated(subset=["symbol", "series", "trading_date"])`

---

## 2. Security Master System — Deferred

The trading engine should **not** use `symbol` as the permanent identity.
Symbols change (relistings, mergers, corporate actions, series changes).

**Target architecture:**

```
security_id
    ↓
security_master
    ├── symbol
    ├── series
    ├── ISIN
    ├── company_identity
    ├── validity_period (active_from, active_to)
    └── exchange

daily_prices
    ↓
security_id + trading_date
```

**Use cases this unlocks:**
- Symbol changes (e.g. company renames)
- Mergers and demergers
- Delistings and relistings
- Corporate actions (splits, bonuses, dividends)
- Series changes (e.g. migration from BL to EQ)

**Status:** Backlogged. Do not implement until the backtesting engine (M0.2)
or corporate actions work explicitly requires it. The current `securities` table
already has `id`, `isin`, `active_from`, `active_to` columns as scaffolding.

---

## 3. Data Layer Abstraction — No Coupling Rule

**Rule (Team Lead directive):**

The backtesting engine must NOT know or care that NSE was the data source.

```python
# WRONG — tight coupling
backtest.py
    └── download_nse_data()

# RIGHT — clean contract
backtest.py
    └── load_market_data()
```

The data layer handles:
- Source selection (NSE, future sources)
- PostgreSQL/TimescaleDB storage
- Dataset versioning
- Price adjustments (raw vs adjusted)

**Contract between Data Engine and Backtest Engine:**

```
              Vishnu
                 │
          NSE Data Engine
                 │
                 ↓
        ┌─────────────────┐
        │ Standardized    │
        │ Market Data API │
        └────────┬────────┘
                 │
                 ↓
              Bharat
                 │
           Backtest Engine
```

**Status:** The standardized API interface should be defined as part of M0.2
before Bharat begins backtest engine integration. The data layer abstraction
is the meeting point between the two systems.

---

## 4. NSE Session Bootstrap — Documented Behavior

**Lesson from M0.1 live testing:**

> The ingestion system must distinguish an HTTP bootstrap response from the
> actual data response.

NSE requires a session cookie obtained by first hitting the homepage.
A bare `raise_for_status()` on the homepage response is acceptable (we need
valid cookies). But the download response must be validated by checking the
content (zip magic bytes `PK`), not just the HTTP status.

**Current implementation:** `NSEDataSource._get_session()` handles the
bootstrap; `download()` validates the response content.

**Regression test:** `tests/test_nse_session.py` — must remain in the suite
permanently.

---

## Changelog

| Date       | Decision                           | Made By |
|------------|------------------------------------|---------|
| 2026-09-18 | Series added to dedup key          | Vishnu  |
| 2026-09-24 | Security identity locked           | Vishnu  |
| 2026-09-24 | Security master deferred           | Vishnu  |
| 2026-09-24 | No-coupling rule for backtest      | Vishnu  |
| 2026-09-24 | Session bootstrap behavior noted   | Vishnu  |
