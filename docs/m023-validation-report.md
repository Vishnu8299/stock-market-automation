# M0.2.3-A — Implementation Validation Report

**Author:** Vishnu  
**Date:** 2026-09-25  
**Commit:** `7943ba6`  
**Status:** VALIDATION COMPLETE — 1 critical bug found and fixed

---

## Executive Summary

The adversarial validation pass **found and fixed a critical bug** in the
adjustment factor engine. This bug would have caused adjusted prices to
still show the split discontinuity, defeating the entire purpose of Hybrid C's
adjusted series.

**The validation pass worked exactly as the Team Lead intended.**

---

## Critical Bug: ex_date Factor Semantics

### What was wrong

The adjustment factor was being applied **on** the ex_date. But on the
ex_date, the market price is **already post-split**. This means:

```
Before fix:
  Day 199 (pre-split):  close=2500  factor=0.2  adj_close=500  OK
  Day 200 (ex_date):    close=500   factor=0.2  adj_close=100  WRONG
  Day 201 (post-split): close=505   factor=1.0  adj_close=505  OK

  Adjusted series STILL shows a discontinuity: 500 -> 100 -> 505
  The SMA would still see a false crash at the split.

After fix:
  Day 199 (pre-split):  close=2500  factor=0.2  adj_close=500  OK
  Day 200 (ex_date):    close=500   factor=1.0  adj_close=500  OK
  Day 201 (post-split): close=505   factor=1.0  adj_close=505  OK

  Adjusted series is CONTINUOUS: 500 -> 500 -> 505
  SMA sees no discontinuity. No false signal.
```

### Root cause

In the backward walk, the factor was updated **before** being assigned to the
date. The fix: assign `factors[d] = cumulative` **first**, then process events
for that date. This way, the event's adjustment only affects dates earlier
than the ex_date.

### Impact

Without this fix, the adjusted series would have been useless for SMA
calculation — the exact problem Hybrid C was designed to solve.

---

## Test Suite Results: 84 passed, 6 skipped, 0 failed

```
tests/test_corporate_actions.py    18 passed   (M0.2.3 base CA tests)
tests/test_m023_validation.py      33 passed   (M0.2.3-A adversarial validation)
tests/test_integration.py          22 passed   (M0.2-I1 integration)
tests/test_ingestion.py             6 passed   (M0.1 parser/validator)
tests/test_nse_session.py           5 passed   (M0.1 NSE bootstrap)
tests/test_db_validation.py         6 skipped  (requires live DB)
---------------------------------------------
TOTAL                              84 passed   0 failed
```

---

## Validation Matrix — All 10 Areas

### 1. Adjustment-factor mathematics PASS

| Test | What it proves |
|---|---|
| `test_2_to_1_split_direction` | 1:2 split -> factor 0.5 for pre-split dates |
| `test_1_to_5_split_direction` | 1:5 split -> factor 0.2 for pre-split dates |
| `test_1_to_1_bonus_direction` | 1:1 bonus -> factor 0.5 (shares double) |
| `test_2_to_1_bonus_direction` | 2:1 bonus -> factor 2/3 (shares x 1.5) |
| `test_sequential_split_then_bonus` | Split day 5, bonus day 8 -> cumulative = 0.25 |
| `test_sequential_bonus_then_split` | Bonus day 5, split day 8 -> same cumulative |
| `test_split_plus_dividend_cumulative` | Split + dividend -> cumulative in DIV scope |
| `test_same_date_two_events` | Two events same date -> both factors multiply |

### 2. OHLC invariants PASS

| Test | What it proves |
|---|---|
| `test_adjusted_ohlc_sanity_after_split` | H >= max(O,C,L), L <= min(O,C,H) for ALL bars |
| `test_adjusted_ohlc_sanity_after_bonus` | Same invariant after bonus adjustment |
| `test_all_four_prices_adjusted` | Open, High, Low, Close ALL adjusted (not just Close) |

### 3. Portfolio value conservation PASS

| Test | What it proves |
|---|---|
| `test_split_value_conservation` | 10x2500 = 25000 -> 50x500 = 25000. Change=0 |
| `test_bonus_value_conservation` | 10x2400 = 24000 -> 20x1200 = 24000. Change=0 |
| `test_2_to_1_bonus_value_conservation` | 100x3000 = 300000 -> 150x2000 = 300000 |
| `test_dividend_creates_cash_not_value` | Shares unchanged, cash = div x shares |

### 4. Signal + event interaction (CRITICAL HYBRID TEST) PASS

| Test | What it proves |
|---|---|
| `test_raw_discontinuity_exists` | Raw prices show ~5x cliff at split — EXPECTED |
| `test_adjusted_continuity_restored` | **Adjusted prices continuous across split** |
| `test_sma_no_false_crossover_on_adjusted` | **No false SMA crash at split boundary** |
| `test_nav_no_artificial_loss` | NAV unchanged: 20x2500 = 100x500 = 50000 |

These 4 tests are the ones that caught the ex_date bug. They now prove
both halves of Hybrid C work together correctly.

### 5. Volume adjustment PASS

| Test | What it proves |
|---|---|
| `test_volume_inverse_adjustment_split` | Pre-split volume x5 (inverse of price /5) |
| `test_volume_unchanged_post_split` | Post-split volume unchanged (factor = 1.0) |

### 6. Event-version reproducibility PASS

| Test | What it proves |
|---|---|
| `test_same_version_same_result` | Same events + same data = identical factors |
| `test_different_event_version_flagged` | Different versions distinguishable |

### 7. Raw data immutability PASS

| Test | What it proves |
|---|---|
| `test_compute_adjusted_preserves_raw` | Input DataFrame untouched after adjustment |
| `test_portfolio_action_does_not_modify_event` | Event object untouched after application |

### 8. Adjusted/raw separation PASS

| Test | What it proves |
|---|---|
| `test_adjusted_columns_separate_from_raw` | Both raw AND adj_ columns exist; they differ pre-split |

### 9. Backward compatibility PASS

| Test | What it proves |
|---|---|
| `test_strategy_runs_without_events` | SMAStrategy works with events=None |
| `test_no_events_means_factor_one` | Empty event list -> all factors = 1.0 |

### 10. Event ordering edge cases PASS

| Test | What it proves |
|---|---|
| `test_event_before_data_window` | Event before first date -> no effect |
| `test_event_after_data_window` | Event after last date -> no effect |
| `test_event_on_first_bar` | Event on day 1 -> factor=1.0 on day 1 (no pre-dates) |
| `test_event_on_last_bar` | Event on last day -> all prior dates adjusted |
| `test_events_input_order_does_not_matter` | Engine sorts internally -> order-independent |

---

## ex_date Semantics Rule (Now Locked)

```
ex_date = the date the action takes effect in the market
        = the date the price ALREADY reflects the action
        = factor = 1.0 on this date

dates BEFORE ex_date = prices do NOT yet reflect the action
                     = factor < 1.0 (adjusted downward)

dates ON OR AFTER ex_date = prices ALREADY reflect the action
                          = factor = 1.0
```

This matches how exchanges define ex_date for all corporate actions.

---

## Facts vs Assumptions

| | Statement | Status |
|---|---|---|
| FACT | Adjustment factor math is correct for splits, bonuses, dividends | 8 tests prove it |
| FACT | OHLC invariants hold after adjustment | 3 tests prove it |
| FACT | Portfolio value is conserved across splits/bonuses | 3 tests prove it |
| FACT | Adjusted series removes split discontinuity | 4 tests prove it (bug found + fixed) |
| FACT | Raw data is never modified | 2 tests prove it |
| FACT | Results are deterministic and reproducible | 2 tests prove it |
| FACT | Engine is backward compatible | 2 tests prove it |
| ASSUMPTION | NSE corporate action data can be parsed into CorporateAction objects | Not yet validated |
| ASSUMPTION | Real NSE corporate actions match our ratio interpretation | Not yet validated |

---

## Remaining Gate: Real NSE Corporate Action Ingestion

The validation pass proves the **calculation engine** is correct.
It does NOT prove the **ingestion pipeline** for real NSE corporate actions exists.

```
NSE Corporate Actions API        <- NOT YET BUILT
        |
Parser                           <- NOT YET BUILT
        |
Normalized CorporateAction       <- Schema exists, engine works
        |
Validation                       <- NOT YET BUILT
        |
Versioned Event Log              <- Schema exists
        |
Adjustment Engine                VALIDATED
        |
Backtester                       VALIDATED
```
