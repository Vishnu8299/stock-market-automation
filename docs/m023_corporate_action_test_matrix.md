# M0.2.3 — Corporate-Action Test Matrix

**Author:** Bharath Kumar Choppa
**Date:** 2026-09-24
**Status:** SPECIFICATION — awaiting team lead approval before implementation
**Depends on:** Bharath Kumar Choppa's M0.2.3 design document (approach A vs B)

---

## Version 1 Scope Declaration

### ✅ SUPPORTED in Version 1

| Event | Mechanical Effect | Rationale |
|---|---|---|
| **Bonus issue** (e.g. 1:1, 2:1) | Price adjusts down, shares multiply | Most common NSE corporate action affecting price series. RELIANCE had one in our test period. |
| **Stock split** (e.g. 2-for-1, 5-for-1) | Price adjusts down, shares multiply | Mechanically identical to bonus for price/share adjustment. Common in Indian equities. |
| **Symbol change** | Ticker changes, price/shares unchanged | Required for security identity continuity across historical data. |

### 🚫 EXPLICITLY REFUSED in Version 1

| Event | Why Deferred | Risk if Encountered |
|---|---|---|
| **Dividend (price adjustment)** | Dividend ex-date price drops are typically small (1-3%). SMA indicators are relatively robust to these. The economic effect (cash receipt) requires a different mechanism than price adjustment. | LOW — small price drops don't typically trigger false SMA crossovers. Sharpe/return will be slightly wrong (price-only vs total-return), but this is documented as a known limitation. |
| **Rights issue** | Conditional participation (subscribe or lapse). TERP calculation is event-specific. Share and cash effects depend on investor decision. | MEDIUM — uncommon but can cause meaningful price discontinuity. If encountered, the backtest should HALT with an explicit error rather than silently produce wrong results. |
| **Merger / demerger** | Security identity changes. One ticker becomes two (demerger) or two become one (merger). Share ratio, price, and even the existence of the security change. | HIGH — but very rare in any single-stock backtest. If encountered, HALT. |
| **Buyback** | Price effect is indirect (tender offer). Participation is conditional. | LOW — typically doesn't cause price discontinuity. |

> [!IMPORTANT]
> **Version 1 rule:** If a corporate action occurs that the system does not support, the backtest MUST fail with an explicit error rather than silently produce wrong results. Silent corruption is unacceptable.

---

## Non-Negotiable Regression Tests

### Test 1: Conservation — 1:1 Bonus Issue

**Purpose:** Verify that economic value is preserved through a 1:1 bonus.

**Synthetic dataset:**

```
Bar  Date        Open     High     Low      Close    Volume
───  ──────────  ───────  ───────  ───────  ───────  ──────
0    2023-09-01  2,400    2,420    2,380    2,400    100000
1    2023-09-04  2,400    2,410    2,390    2,405    100000
2    2023-09-05  2,405    2,415    2,395    2,410    100000
3    2023-09-06  2,410    2,420    2,400    2,408    100000
4    2023-09-07  2,408    2,418    2,398    2,412    100000
                          ─── BONUS EX-DATE ───
5    2023-09-08  1,206    1,212    1,200    1,205    200000
6    2023-09-11  1,205    1,210    1,198    1,208    200000
7    2023-09-12  1,208    1,215    1,202    1,210    200000
8    2023-09-13  1,210    1,218    1,205    1,212    200000
9    2023-09-14  1,212    1,220    1,208    1,215    200000
```

**Corporate action event:**
```
security: TEST_STOCK
action_type: BONUS
ratio: 1:1 (1 new share for every 1 held)
ex_date: 2023-09-08
```

**Setup:**
- Initial capital: 100,000
- Buy 10 shares at bar 1 open (2,400) via Buy & Hold
- Cost: 0.1% = 24.00
- Cash after buy: 100,000 - 24,000 - 24 = 75,976

**Expected behavior:**

| Checkpoint | Before bonus (bar 4) | After bonus (bar 5) | Pass criterion |
|---|---|---|---|
| Shares held | 10 | **20** | Doubled |
| Share price (close) | 2,412 | 1,205 | ~Halved |
| Holdings value | 10 × 2,412 = 24,120 | 20 × 1,205 = 24,100 | **Within ±0.5% of pre-bonus value** |
| Portfolio equity | 75,976 + 24,120 = 100,096 | 75,976 + 24,100 = 100,076 | **No phantom drawdown > 1%** |
| Cash | 75,976 | 75,976 | **Unchanged** |
| avg_cost | 2,400 | **1,200** | Halved |
| Trade log | 1 BUY | 1 BUY | **No spurious trades generated** |

**Pass/fail:**
```
PASS if:
  abs(equity_before_bonus - equity_after_bonus) / equity_before_bonus < 0.01
  shares_after == shares_before * 2
  avg_cost_after == avg_cost_before / 2
  cash_after == cash_before
  len(trade_log) unchanged
  
FAIL if:
  equity shows > 1% change on bonus ex-date
  shares not doubled
  any phantom trade recorded
```

> [!CAUTION]
> The ±0.5% tolerance on holdings value accounts for genuine market movement between the close on cum-date and the open on ex-date. The bonus itself should contribute ZERO to the equity change.

---

### Test 2: Conservation — 2:1 Stock Split

**Purpose:** Verify same conservation for a split (mechanically similar to bonus but different corporate-action type).

**Synthetic dataset:** Same structure as Test 1, but:
- Pre-split close: ~2,400
- Post-split open: ~1,200
- Split ratio: 2-for-1

**Corporate action event:**
```
security: TEST_STOCK
action_type: SPLIT
ratio: 2:1 (each share becomes 2 shares)
ex_date: 2023-09-08
```

**Expected behavior:** Identical to Test 1:
- Shares: 10 → 20
- avg_cost: 2,400 → 1,200
- Equity: preserved within ±0.5%
- Cash: unchanged
- No phantom trades

---

### Test 3: No Phantom SMA Death Cross

**Purpose:** Verify that a corporate action does NOT generate a false SMA crossover signal.

**Synthetic dataset:**

A gentle uptrend with a 1:1 bonus in the middle. The key property: on an **adjusted** price series, the trend is continuous with NO crossover.

```
Construction rules:
  - 250 bars of gentle uptrend: close_raw = 2000 + (i * 2)
  - Bonus ex-date at bar 150
  - Pre-bonus prices: as computed
  - Post-bonus prices: (2000 + i*2) / 2  (halved by bonus)
  
  On adjusted series (all prices halved retroactively):
    adjusted_close = (2000 + i*2) / 2 = 1000 + i
    This is a steady uptrend from 1000 to 1250.
    SMA-50 is always below the price (lagging in uptrend).
    SMA-200 is always below SMA-50.
    NO CROSSOVER SHOULD OCCUR.
```

**Setup:**
- Run SMA 50/200 on this data
- Whatever adjustment mechanism is used (Approach A or B), the system must NOT generate a death cross

**Pass/fail:**
```
PASS if:
  No SELL signal generated anywhere in the 250 bars
  The only signal is BUY (when SMA-50 first crosses above SMA-200)
  
FAIL if:
  Any SELL signal appears
  Any false BUY signal appears after the initial entry
  The signal sequence differs from what would be produced
    on the correctly adjusted series
```

---

### Test 4: No Phantom Drawdown

**Purpose:** Verify that the equity curve does NOT show a phantom drawdown from a corporate action.

**Synthetic dataset:** Same as Test 3 (gentle uptrend + bonus).

**Setup:**
- Buy & Hold strategy, 10 shares bought on bar 1
- 1:1 bonus at bar 150

**Expected behavior:**

```
Equity curve should show:
  - Steady increase from bar 1 to bar 249
  - NO drop > 1% on the bonus ex-date
  - max_drawdown should reflect ONLY genuine market dips,
    NOT the corporate-action price adjustment
```

**Pass/fail:**
```
PASS if:
  max_drawdown > -1%  (uptrend should have minimal drawdown)
  No single-day equity drop > 1% on bonus ex-date
  equity_curve is monotonically non-decreasing within tolerance
  
FAIL if:
  max_drawdown shows ~-50% phantom drawdown
  A single-day -50% equity drop appears on ex-date
```

---

### Test 5: P&L Correctness Through Bonus

**Purpose:** Verify that realized P&L is computed correctly when a trade spans a corporate action.

**Synthetic dataset:**

```
Bar  Date        Close    Event
───  ──────────  ───────  ─────────────
0    2023-09-01  2,400    
1    2023-09-04  2,400    BUY signal (SMA golden cross)
2    2023-09-05  2,405    Fill at bar 2 open = 2,400
...
50   2023-11-15  1,250    BONUS 1:1 occurs at bar 50
...
100  2024-01-10  1,350    SELL signal (SMA death cross)
101  2024-01-11  1,348    Fill at bar 101 open = 1,350
```

**Setup:**
- Buy 10 shares at 2,400 (bar 2 open)
- Bonus at bar 50: 10 → 20 shares, avg_cost 2,400 → 1,200
- Sell 20 shares at 1,350 (bar 101 open)

**Expected P&L:**
```
Correct:
  Buy:  10 shares × 2,400 = 24,000 (+ cost)
  Bonus: 10 → 20 shares, avg_cost → 1,200
  Sell: 20 shares × 1,350 = 27,000 (- cost)
  Gross P&L = 27,000 - 24,000 = +3,000
  Per-share P&L = (1,350 - 1,200) × 20 = +3,000 ✓
  
WRONG (current system):
  Buy:  10 shares × 2,400 = 24,000
  No bonus adjustment
  Sell: 10 shares × 1,350 = 13,500
  Gross P&L = 13,500 - 24,000 = -10,500
  Per-share P&L = (1,350 - 2,400) × 10 = -10,500 ✗
```

**Pass/fail:**
```
PASS if:
  realized_pnl ≈ +3,000 (minus costs)
  sell_quantity == 20 (adjusted shares)
  
FAIL if:
  realized_pnl ≈ -10,500
  sell_quantity == 10 (unadjusted)
```

> [!WARNING]
> This test explicitly demonstrates why the current engine would produce a **₹13,500 error** on a single round-trip through a bonus event. The sign of the P&L flips from profit to loss.

---

### Test 6: Avg-Cost Adjustment Precision

**Purpose:** Verify that `avg_cost` adjusts correctly for various bonus/split ratios, including non-1:1 ratios.

**Test cases (unit-level, no data feed needed):**

| Scenario | Pre-action shares | Pre-action avg_cost | Action | Post-action shares | Post-action avg_cost | Total cost basis |
|---|---|---|---|---|---|---|
| 1:1 bonus | 10 | 2,400 | BONUS 1:1 | 20 | 1,200 | 24,000 |
| 2:1 split | 10 | 2,400 | SPLIT 2:1 | 20 | 1,200 | 24,000 |
| 5:1 split | 10 | 2,400 | SPLIT 5:1 | 50 | 480 | 24,000 |
| 1:2 bonus | 10 | 2,400 | BONUS 1:2 | 15 | 1,600 | 24,000 |
| 3:1 bonus | 10 | 2,400 | BONUS 3:1 | 40 | 600 | 24,000 |

**Conservation invariant (must hold for ALL rows):**
```
post_shares × post_avg_cost == pre_shares × pre_avg_cost
```

This is the **total cost basis conservation rule**. It must hold exactly (not approximately) since no cash changes hands.

---

## Deferred Events — What Version 1 Explicitly Refuses

### Dividends

| Aspect | Version 1 behavior | Why acceptable for now |
|---|---|---|
| Price adjustment | NOT applied. Dividend ex-date gap remains in raw prices. | Dividend drops are typically 1-3%, small enough that SMA crossovers are rarely triggered. |
| Cash receipt | NOT modeled. | Our benchmark is price-only return, not total return. This is documented and consistent across SMA and B&H. |
| Total-return comparison | NOT supported. | Deferred to M0.2.4 (benchmark normalization). |

**When this becomes a problem:** When we compare against NIFTY 50 Total Return Index, or when backtest horizons exceed 5 years (cumulative dividend effect becomes material).

**Safeguard:** If the system detects a DIVIDEND corporate action in the data, it should log a WARNING but NOT halt. The warning should state: "Dividend on [date] for [symbol] not modeled. Returns are price-only."

### Rights Issues

**Version 1 behavior:** If a rights issue is detected in the corporate-action data, the backtest MUST HALT with an error:

```
ERROR: Unsupported corporate action 'RIGHTS_ISSUE' for TEST_STOCK on 2024-03-15.
Version 1 does not support rights issues. This backtest cannot proceed.
Exclude this security or this date range.
```

**Rationale:** Rights issues involve conditional participation. Modeling whether the portfolio subscribes (and at what price) requires a decision rule we haven't designed. Silent pass-through would produce wrong results.

### Mergers / Demergers

**Version 1 behavior:** HALT with error, same as rights issues.

```
ERROR: Unsupported corporate action 'DEMERGER' for RELIANCE on 2024-10-01.
Version 1 does not support mergers/demergers. Exclude this security.
```

### Symbol Changes

**Version 1 behavior:** Supported via a mapping table. The system should accept:

```
security: VEDL (formerly SESAGOA)
action_type: SYMBOL_CHANGE
old_symbol: SESAGOA
new_symbol: VEDL
effective_date: 2015-06-01
```

And treat all data for SESAGOA before 2015-06-01 as belonging to VEDL.

**Test:** A backtest requesting `VEDL` with a start date before 2015-06-01 should seamlessly load SESAGOA data for the earlier period and VEDL data for the later period.

---

## Test Infrastructure Requirements

### Synthetic Data Generator

All tests above require synthetic datasets with known properties. I propose a test utility:

```python
def make_bonus_dataset(
    n_bars: int,
    pre_bonus_price: float,
    bonus_ratio: tuple[int, int],  # (new_shares, per_existing)
    bonus_bar: int,
    trend_per_bar: float = 0.0,    # daily price change for gentle trends
) -> tuple[pd.DataFrame, dict]:
    """
    Generate a synthetic OHLCV dataset with a single bonus event.
    
    Returns:
        (ohlcv_dataframe, corporate_action_event_dict)
    """
```

This keeps test data explicit and reproducible rather than depending on real market data where we can't control what other factors are present.

### Test Organization

```
tests/
  test_backtesting.py          # existing 28 tests (unchanged)
  test_corporate_actions.py    # NEW — all 6 tests above
    TestConservation
      test_bonus_1_1_wealth_preserved
      test_split_2_1_wealth_preserved
    TestNoPhantomSignals
      test_no_false_death_cross_through_bonus
      test_no_phantom_drawdown_through_bonus
    TestPnLCorrectness
      test_pnl_correct_through_bonus
    TestAvgCostAdjustment
      test_avg_cost_conservation_invariant  (parametrized across ratios)
```

---

## Dependency on Design Document

These tests are **approach-agnostic** — they specify WHAT the system must do, not HOW.

Whether the design recommends Approach A (adjusted series) or Approach B (event-driven simulation):
- All 6 tests must pass
- The conservation invariant must hold
- No phantom signals may appear

The tests can be written now as failing tests (red), then made green by whichever implementation is chosen.

---

## Summary

| Category | Tests | Non-negotiable? |
|---|---|---|
| Conservation (wealth preservation) | 2 | ✅ Yes |
| Phantom signals (no false crossovers) | 2 | ✅ Yes |
| P&L correctness | 1 | ✅ Yes |
| Avg-cost adjustment precision | 1 (parametrized × 5) | ✅ Yes |
| **Total** | **6 test functions, 10 scenarios** | |

| Scope | Version 1 |
|---|---|
| Bonus issues | ✅ Supported |
| Stock splits | ✅ Supported |
| Symbol changes | ✅ Supported |
| Dividends | ⚠️ Logged warning, not modeled |
| Rights issues | 🚫 Halt with error |
| Mergers/demergers | 🚫 Halt with error |
| Buybacks | 🚫 Halt with error |
