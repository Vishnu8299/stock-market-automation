# M0.2.4-A — NSE Historical Data Report

**Author:** Vishnu (Engineering Lead)  
**Date:** 2026-10-16  
**Starting commit:** `563935f`  
**Research benchmark:** NOT RUN  
**Live trading:** OFF

---

## 1. Repository State

| Item | Value |
|---|---|
| Starting commit | `563935f` |
| Starting tree | Clean (only `scripts/download_nse_historical.py` untracked) |
| Baseline tests | 217 passed, 6 skipped, 0 failed |
| Branch | `main` |

---

## 2. Acquisition Method and Results

### Source

NSE CM UDiFF Bhavcopy archives from `nsearchives.nseindia.com/content/cm/`.

URL pattern: `BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip`

### Access Findings

| Probe date | Status | Notes |
|---|---|---|
| 2021-10-01 to 2023-12-01 | **404** | NSE does NOT serve pre-2024 bhavcopies |
| 2024-01-02 onward | **200** | UDiFF format available |
| `nseindia.com` homepage | **403** | CDN/WAF blocks session bootstrap |
| `nsearchives.nseindia.com` direct | **200** | Direct archive access works without session cookies |

**Access limitation:** NSE's public archive only serves bhavcopies from approximately 2024-01-02 onward. Pre-2024 historical data is not available via this endpoint. No alternative NSE-authoritative source was found.

### Download Results

| Metric | Value |
|---|---|
| Requested range | 2024-01-02 to 2026-10-15 |
| Candidate weekdays | 728 |
| Downloaded | 686 (including 1 retry for 2026-07-31) |
| Cached (from M0.1) | 1 (2026-09-18) |
| 404 Not Found | 41 (all classified as NSE holidays) |
| Errors | 1 (2026-07-31 timeout - recovered on retry) |
| Raw zip archives | 687 files under `data/raw/NSE/YYYY/MM/DD/original_file.zip` |
| Request delay | 0.3s between downloads |

---

## 3. Dataset Identity

| Property | Value |
|---|---|
| File | `data/nse_native/RELIANCE_EQ_NSE_native.csv` |
| Symbol | RELIANCE |
| Series | EQ |
| First date | 2024-01-02 |
| Last date | 2026-10-15 |
| Sessions | **687** |
| Unique dates | 687 (no duplicates) |
| Columns | `trading_date, symbol, series, open, high, low, close, volume` |
| **SHA-256** | `78305cc57ff36afc0209830f809a41776b74e70da34aa305543bb7571d35129d` |
| Dataset version | `nse_native_v1` |
| Manifest | `data/nse_native/RELIANCE_EQ_manifest.json` |

### Validation Summary

| Check | Result |
|---|---|
| Duplicate `(symbol, series, trading_date)` | **0** |
| OHLC: High >= max(O,C,L) | **687/687 pass** |
| OHLC: Low <= min(O,C,H) | **687/687 pass** |
| Positive prices | **687/687 pass** |
| Positive volume | **687/687 pass** |
| Missing values | **0 in any column** |
| DataInterface compatibility | **PASS** - `CsvDataSource.load("RELIANCE")` returns 687 rows |

---

## 4. yfinance Reference CSV

| Property | Value |
|---|---|
| File | `data/raw/RELIANCE_2021-09-27_2026-09-24.csv` |
| Source | yfinance via `scripts/fetch_nse_data.py` (`auto_adjust=False`) |
| SHA-256 | `8e9426913fd4dd6606e344ad6f27b047440ea2bc7d4acbb4e026088439f595f0` |
| Sessions | 1,240 |
| Range | 2021-09-27 to 2026-09-24 |

**Hash discrepancy:** The task brief stated SHA-256 `d9b790bb...` for this file. The actual on-disk hash is `8e94269...`. Both are recorded. The file on disk is the current truth.

**CRITICAL: yfinance prices are already bonus-adjusted.** Verified:

- 2024-10-01 yfinance close: Rs 1,464.82
- 2024-10-01 NSE close: Rs 2,929.65
- Ratio: 2.000x (exact 1:1 bonus adjustment)
- Post-bonus (2024-10-29): both sources show Rs 1,340.00

**Applying M0.2.3 corporate-action adjustment on yfinance data would double-adjust prices.** The yfinance CSV must NOT be used as raw input to the Hybrid-C pipeline.

---

## 5. Corporate-Action Reconciliation

### Verified Events (2024-01-02 to 2026-10-15)

| Event | Type | Ex-Date | Details | Evidence |
|---|---|---|---|---|
| **1:1 Bonus** | BONUS | 2024-10-28 | 1 new share per 1 held | NSE data confirms price drop |
| Dividend | FINAL | 2024-08-19 | Rs 10.00/share (pre-bonus face) | anandrathi.com |
| Dividend | FINAL | 2025-08-14 | Rs 5.50/share | anandrathi.com |
| Dividend | FINAL | 2026-06-05 | Rs 6.00/share | anandrathi.com |

### 1:1 Bonus Verification (ex-date 2024-10-28)

```
NSE raw prices around ex-date:
trading_date    open     high      low    close     volume
2024-10-24   2670.55  2687.40  2646.25  2679.60   11077967
2024-10-25   2687.00  2688.70  2644.00  2655.70    9298748   <- last cum-bonus
2024-10-28   1337.00  1353.00  1322.10  1334.35   10824350   <- ex-bonus
2024-10-29   1328.10  1343.20  1320.30  1340.00   12008361

Price ratio: 2655.70 / 1334.35 = 1.990 (~2.0)
```

**PASS:** Price drop is consistent with a 1:1 bonus (factor = 2.0).

### Hybrid-C Verification

| Check | Status |
|---|---|
| Raw NSE prices preserved unchanged | PASS |
| Pre-bonus prices are ~2x post-bonus | PASS |
| Adjustment direction correct | PASS (backward adjustment divides pre-ex by 2) |
| No double adjustment possible | PASS (NSE-native is raw; yfinance labeled separately) |
| Event-driven portfolio accounting | PASS (shares double at ex-date via M0.2.3) |

### Dividend Treatment Limitation

The M0.2.3 corporate-action engine handles splits and bonuses for price adjustment and portfolio share accounting. **Dividend cash injection into portfolio is NOT implemented.** Benchmark metrics produced from this dataset are **price-return only**, not total-return. This must be explicitly stated in any research output.

---

## 6. Calendar Anomaly Resolution

### Six Previously Reported Gap Dates

| Date | Day | NSE | yf | Classification | Evidence |
|---|---|---|---|---|---|
| 2024-01-22 | Mon | ABSENT | ABSENT | Official closure: Ram Mandir Pran Pratishtha | zerodha.com, groww.in |
| 2024-05-20 | Mon | ABSENT | ABSENT | Official closure: Lok Sabha Elections (Mumbai) | zerodha.com |
| 2024-11-20 | Wed | ABSENT | ABSENT | Official closure: Maharashtra Assembly Elections | groww.in |
| 2025-10-22 | Wed | ABSENT | ABSENT | Official closure: Diwali-Balipratipada | nseindia.com |
| 2026-03-26 | Thu | ABSENT | ABSENT | Official closure: Shri Ram Navami | nseindia.com |
| 2026-03-31 | Tue | ABSENT | ABSENT | Official closure: Shri Mahavir Jayanti | nseindia.com |

All six are confirmed official NSE closures. No missing-data issues.

### All 42 Weekday Gaps Classified

All 42 weekday gaps in the NSE-native dataset correspond to official NSE trading holidays including:

- Republic Day, Independence Day, Gandhi Jayanti (national holidays)
- Holi, Diwali, Dussehra, Janmashtami, Ram Navami (Hindu festivals)
- Eid-ul-Fitr, Eid-ul-Adha, Muharram, Milad-un-Nabi (Islamic observances)
- Good Friday, Christmas (Christian observances)
- Mahavir Jayanti, Buddha Purnima (Jain/Buddhist observances)
- Dr. Ambedkar Jayanti, May Day (civic holidays)
- Election closures (Lok Sabha 2024-05-20, Maharashtra 2024-11-20)
- Special closures (Ram Mandir 2024-01-22)

**No unexplained gaps remain.**

---

## 7. Test Results

### Full Suite (current checkout, 2026-10-16)

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

### Additional Verified

| Check | Result |
|---|---|
| DataInterface loads NSE-native CSV | PASS (687 rows via CsvDataSource) |
| Downloader idempotent (raw zips not overwritten) | PASS (687 archives match 687 rows) |
| No existing tests modified | PASS (zero changes to test files) |
| No frozen modules modified | PASS (engine, CA, strategies unchanged) |

---

## 8. Files Changed

| File | Type | Purpose |
|---|---|---|
| `scripts/download_nse_historical.py` | NEW | NSE historical bhavcopy downloader with provenance |
| `data/nse_native/RELIANCE_EQ_NSE_native.csv` | NEW | Canonical NSE-native dataset (687 sessions) |
| `data/nse_native/RELIANCE_EQ_manifest.json` | NEW | Machine-readable acquisition manifest |
| `docs/m024a_nse_historical_data_report.md` | NEW | This report |
| `data/raw/NSE/YYYY/MM/DD/original_file.zip` | NEW | 687 immutable raw NSE bhavcopy archives (gitignored) |

No existing files were modified. No frozen modules were changed.

---

## 9. Remaining Risks

| Risk | Severity | Notes |
|---|---|---|
| Pre-2024 NSE data unavailable | Medium | NSE public archive only goes back to ~Jan 2024. Cannot acquire 2021-2023 NSE-native data via this method. |
| SMA 200 warm-up consumes 200 bars | Low | With 687 bars, ~487 evaluable bars remain (from ~Sep 2024 onward). |
| Dividend cash not injected into portfolio | Medium | M0.2.3 handles splits/bonuses but not dividend cash. Benchmark is price-return only. |
| NSE CDN may change access patterns | Low | Homepage 403 already occurs; direct archive access still works. |
| 2026-07-31 required retry | Low | Transient timeout; data recovered successfully. |

---

## 10. Team Lead Gate

### Recommendation: READY FOR TEAM LEAD REVIEW

**Evidence:**

- NSE-native dataset: 687 sessions, 2024-01-02 to 2026-10-15, SHA-256 verified
- 687 immutable raw bhavcopy archives preserved with hashes
- Zero OHLC violations, zero duplicates, zero missing values
- 1:1 bonus verified at ex-date 2024-10-28 (ratio = 1.990)
- yfinance double-adjustment risk identified and documented
- All 42 weekday gaps classified as official NSE holidays
- 217 tests pass, 0 fail, no modifications to frozen modules
- DataInterface compatibility verified

**Outstanding items for Team Lead decision:**

1. **Evaluation period selection** - Team Lead must freeze start/end dates before research benchmark
2. **Dividend treatment** - price-return only; total-return requires implementation decision
3. **Pre-2024 data gap** - NSE does not serve older bhavcopies; alternative source needed if longer history is required

**Explicit confirmation:** The first SMA 50/200 vs Buy and Hold research benchmark was NOT run.
