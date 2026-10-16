# M0.2.4-A — Final Integrity Gate

**Author:** Vishnu (Engineering Lead)  
**Date:** 2026-10-16  
**Commit under review:** `2889ce0`  
**Research benchmark:** NOT RUN  
**Evaluation period:** NOT FROZEN  
**Live trading:** OFF

---

## 1. Repository State

| Check | Actual | Status |
|---|---|---|
| Local HEAD | `2889ce0d1e4ea20358715b1e7a807a80820963cc` | Verified |
| Remote `origin/main` | `2889ce0d1e4ea20358715b1e7a807a80820963cc` | Verified |
| Local == Remote | Identical | PASS |
| Working tree | Clean (no untracked, no modified) | PASS |
| Commit `2889ce0` files | 4 new files, 8329 insertions | Verified |

```
git show --stat --oneline 2889ce0:
  data/nse_native/RELIANCE_EQ_NSE_native.csv |  688 +++
  data/nse_native/RELIANCE_EQ_manifest.json  | 7105 +++
  docs/m024a_nse_historical_data_report.md   |  260 +
  scripts/download_nse_historical.py         |  276 ++
```

---

## 2. Canonical Dataset Integrity

File: `data/nse_native/RELIANCE_EQ_NSE_native.csv`

| Check | Expected | Actual | Status |
|---|---|---|---|
| SHA-256 | `78305cc5...` | `78305cc57ff36afc0209830f809a41776b74e70da34aa305543bb7571d35129d` | **PASS** |
| Rows | 687 | 687 | **PASS** |
| Columns | trading_date,symbol,series,open,high,low,close,volume | Exact match | **PASS** |
| First date | 2024-01-02 | 2024-01-02 | **PASS** |
| Last date | 2026-10-15 | 2026-10-15 | **PASS** |
| Date ordering | Ascending | Ascending | **PASS** |
| Unique (sym,series,date) | 687 | 687, 0 duplicates | **PASS** |
| Symbol values | RELIANCE only | RELIANCE only | **PASS** |
| Series values | EQ only | EQ only | **PASS** |
| OHLC: High >= max(O,C,L) | 687/687 | 687/687 | **PASS** |
| OHLC: Low <= min(O,C,H) | 687/687 | 687/687 | **PASS** |
| Positive prices | 687/687 | 687/687 | **PASS** |
| Positive volume | 687/687 | 687/687 | **PASS** |
| Missing values | 0 | 0 | **PASS** |
| Hash matches manifest | Yes | Yes | **PASS** |
| DataInterface loads | 687 rows | 687 rows via CsvDataSource | **PASS** |

---

## 3. Provenance and Raw Archive Verification

| Check | Actual | Status |
|---|---|---|
| Manifest file entries | 687 | **PASS** |
| Raw archives on disk | 687 | **PASS** |
| Manifest sessions matches CSV rows | 687 == 687 | **PASS** |
| Dataset dates with no archive | 0 | **PASS** |
| Archives with no dataset row | 0 | **PASS** |

### Spot-check: 5 random manifest entries vs raw files (seed=42)

| Date | Archive exists | Hash matches manifest | Status |
|---|---|---|---|
| 2026-08-31 | Yes | Yes | **PASS** |
| 2024-06-24 | Yes | Yes | **PASS** |
| 2024-02-08 | Yes | Yes | **PASS** |
| 2025-02-20 | Yes | Yes | **PASS** |
| 2025-01-08 | Yes | Yes | **PASS** |

### Spot-check: 3 random raw archives parsed for RELIANCE/EQ

All three archives opened successfully, contained UDiFF-format CSV, and each had exactly 1 RELIANCE/EQ row. **PASS.**

---

## 4. Downloader Safety

File: `scripts/download_nse_historical.py`

| Check | Finding |
|---|---|
| Idempotent reruns | PASS: checks `out_path.exists()` before download (line 81-83) |
| HTTP error handling | PASS: 404, non-200, non-zip responses all handled distinctly |
| Timeout handling | PASS: `timeout=30` on GET, caught in try/except |
| Retry behavior | No automatic retry (manual retry for 2026-07-31 succeeded) |
| Archive integrity | PASS: checks PK magic bytes before saving |
| 404 classification | Correctly records as `not_found` in manifest errors, does NOT label as holiday |
| Deterministic output | PASS: sorted by trading_date, reset_index(drop=True) |
| Provenance | PASS: manifest records URL, hash, size, timestamp for each file |
| Existing data safety | PASS: never overwrites existing zip files |

---

## 5. Evaluation-Period Feasibility (Static Inspection Only)

Proposed (NOT yet approved):
- Warm-up: 2024-01-02 to 2024-12-31
- Evaluation: 2025-01-01 to 2026-09-30

| Check | Actual | Status |
|---|---|---|
| Warm-up bars available | 245 | **PASS** (>= 200 needed for SMA-200) |
| Evaluation bars available | 432 | **PASS** |
| Dataset extends past eval end | 2026-10-15 > 2026-09-30 | **PASS** |
| 2025-01-01 in dataset | Yes (trading day) | **PASS** |
| BenchmarkConfig supports explicit dates | Yes: `start_date`, `end_date` fields | **PASS** |
| Both strategies use same dates | Yes: same `start`/`end` to `run_backtest()` | **PASS** |
| Same data source | Yes: single `data_source` instance | **PASS** |
| Same cost model | Yes: single `cost_model` instance | **PASS** |
| Same corporate actions | Yes: same `corporate_actions` list | **PASS** |
| Data hash recorded | Yes: SHA-256 in BenchmarkReport.data_hash | **PASS** |

### Warm-up/evaluation boundary limitation

The engine does **NOT** have a separate warm-up concept. Both strategies receive the same data from `start_date` to `end_date`. Implications:

1. **BuyAndHold** buys on bar 0 (the first bar of `start_date`). If `start_date=2024-01-02`, B&H is fully invested from 2024-01-02.

2. **SmaCrossover** produces HOLD for the first 200 bars (SMA-200 needs 200 data points). With `start_date=2024-01-02`, the first possible SMA signal is around bar 200 (~Sep 2024).

3. **Metrics** (return, CAGR, drawdown, Sharpe) are computed over the **entire** `start_date` to `end_date` range for both strategies.

4. **Consequence**: B&H has ~200 bars of invested exposure before SMA can produce its first signal. This is an inherent property of comparing a momentum strategy requiring warm-up against a non-indicator strategy. It is **not a software defect** — the M0.2.4 design explicitly states "realized exposure is an observed metric, not a controlled equality."

5. **If the Team Lead wants evaluation-only metrics** (excluding warm-up), the engine would need modification to support a separate `eval_start` parameter. This modification is NOT authorized in the current milestone.

**Recommendation:** The Team Lead should be aware that if `start_date=2024-01-02`, the entire 2024-01-02 to `end_date` range is evaluated, including the SMA warm-up period. Setting `start_date` further back to 2024-01-02 while setting `end_date` to 2026-09-30 means the comparison includes ~200 bars where SMA holds cash while B&H is invested. The `avg_exposure_pct` metric captures this difference.

---

## 6. Dividend Treatment

| Check | Finding |
|---|---|
| `portfolio.py` contains "dividend" | Line 192: comment `# No adjustment needed (e.g. symbol change, dividend)` |
| Dividend cash injection code | **NOT PRESENT** |
| `execution.py` dividend handling | **NOT PRESENT** |
| Classification | **Confirmed: price-return only, NOT total-return** |

The frozen M0.2.3 logic explicitly skips dividends. Any benchmark results must be labeled as price-return performance.

---

## 7. Test Results (actual, 2026-10-16)

```
tests/test_backtesting.py              26 passed
tests/test_benchmark.py                29 passed
tests/test_corporate_actions.py        18 passed
tests/test_db_validation.py             6 skipped (requires live DB)
tests/test_ingestion.py                 6 passed
tests/test_integration.py              22 passed
tests/test_m023_validation.py          33 passed
tests/test_nse_corporate_actions.py    48 passed
tests/test_nse_session.py               5 passed
tests/test_real_corporate_actions.py   30 passed
----------------------------------------------
TOTAL                                 217 passed, 6 skipped, 0 failed
```

No test files were modified. No frozen modules were changed.

---

## 8. Discrepancies and Risks

| Issue | Severity | Detail |
|---|---|---|
| yfinance CSV hash differs from task brief | Low | Task brief stated `d9b790bb...`; actual file on disk is `8e94269...`. File on disk is the current truth. No action needed since yfinance CSV is reference-only. |
| No pre-2024 NSE data | Medium | NSE public archive returns 404 for all dates before 2024-01-02. If longer history is needed, an alternative source is required. |
| No automatic retry in downloader | Low | 2026-07-31 required manual retry after timeout. For future bulk downloads, automatic retry with backoff would be preferable. |
| Engine has no warm-up/evaluation split | Medium | See Section 5. Both strategies see the same date range; metrics cover the full range including SMA warm-up bars. |
| Dividend cash not injected | Medium | Benchmark is price-return only. Does not affect correctness of the comparison, but must be disclosed. |

---

## 9. Verdict

### READY FOR TEAM LEAD APPROVAL

**Evidence summary:**

- Dataset SHA-256 verified: `78305cc5...` matches manifest
- 687 rows, 687 raw archives, 687 manifest entries: all consistent
- Zero OHLC violations, zero duplicates, zero missing values
- 5/5 random hash spot-checks PASS
- 3/3 random archive parse spot-checks PASS
- Full provenance traceability: every dataset row maps to a raw archive
- 217 tests pass, 0 fail, no frozen modules modified
- Local and remote commits identical: `2889ce0`
- Working tree clean

**Team Lead decisions required before first benchmark:**

1. **Freeze the evaluation period.** The proposed 2024-01-02 to 2026-09-30 window is feasible (245 warm-up + 432 eval bars).
2. **Acknowledge warm-up inclusion.** The engine does not separate warm-up from evaluation. B&H is invested ~200 bars before SMA can trade. The `avg_exposure_pct` metric captures this difference.
3. **Accept price-return only.** No dividend cash is injected.

**Explicit confirmations:**
- The first SMA 50/200 vs Buy and Hold research benchmark was **NOT run**.
- The evaluation period remains **UNFROZEN**.
- No strategy performance was inspected.
- No SMA parameters were optimized.
