# M0.2.3 -- Corporate-Action Design Document

**Author:** Bharath Kumar Choppa
**Date:** 2026-09-25
**Status:** DESIGN -- awaiting team lead approval
**Prerequisites:** Bharath's impact review (accepted), test matrix (approved)

---

## Central Question

> **Do we adjust prices before the backtester, inject corporate-action events into the simulation, or support both?**

---

## Three Distinct Concepts

The team lead requires these to remain separate throughout the design:

| Concept | What it represents | Example |
|---|---|---|
| **Price history** | What the exchange reported (raw) or what a researcher computed (adjusted) | Raw close: 2,400 pre-bonus; Adjusted close: 1,200 retroactively |
| **Portfolio position** | What the investor actually holds | 10 shares becomes 20 shares on bonus |
| **Total-return performance** | Economic return including distributions | Price return + dividends received |

These are NOT the same thing. A bonus changes price history and portfolio position but not total-return. A dividend changes total-return but typically doesn't require retroactive price adjustment for indicator computation.

---

## Approach A -- Adjusted Research Price Series

```
Raw NSE prices (daily_prices table, never modified)
      |
      v
Corporate actions (corporate_actions table)
      |
      v
Adjustment engine (new module)
      |
      v
Adjusted research series (new table / DataFrame)
      |
      v
Backtester (SMA computed on adjusted series)
      |
      v
Results (all on adjusted-price basis)
```

### How it works

1. Load raw prices from `daily_prices`
2. Load corporate actions for the security and date range
3. For each action (working backward from most recent):
   - Compute an adjustment factor (e.g., bonus 1:1 → factor = 0.5)
   - Multiply all prices BEFORE the ex-date by the factor
   - Multiply all volumes BEFORE the ex-date by 1/factor
4. Feed the adjusted series to the backtester
5. The backtester runs exactly as today -- no changes to execution, portfolio, or metrics

### What it solves

| Problem from impact review | Solved? | How |
|---|---|---|
| S1: SMA on raw close | ✅ | SMA sees continuous adjusted prices |
| S2: Spurious crossover | ✅ | No price discontinuity means no false signals |
| P3: Equity mark-to-market | ❌ | Portfolio still holds 10 shares at avg_cost 2,400 |
| P1: No position adjustment | ❌ | Portfolio doesn't know about the bonus |
| P2: Wrong avg_cost | ❌ | avg_cost still 2,400 on adjusted scale where price is 1,200 |
| M1: Phantom drawdown | ❌ | Equity = cash + 10 × adjusted_price; correct direction but wrong magnitude if shares weren't doubled |

### Critical gap

**Approach A fixes signals but NOT portfolio accounting.** If we buy at adjusted price 1,200 (which was really 2,400 raw) and the portfolio stores avg_cost = 1,200 (adjusted), then after the bonus the portfolio holds 10 shares with avg_cost 1,200. But it should hold 20 shares with avg_cost 600 (adjusted) or 20 shares with avg_cost 1,200 (raw).

The portfolio doesn't know a bonus happened, so it never doubles the shares.

**Verdict: Approach A alone is insufficient.** It solves half the problem (indicators) but leaves the other half (portfolio accounting) broken.

---

## Approach B -- Event-Driven Simulation

```
Raw prices ─────────────────────────> Backtester
                                          |
Corporate-action events ─────────────────>|
                                          v
                               Portfolio adjustment
                               (double shares, halve avg_cost)
                                          |
                                          v
                                     Results
```

### How it works

1. Load raw prices (no adjustment)
2. Load corporate actions for the security and date range
3. The execution simulator receives both the signal DataFrame AND a list of corporate-action events
4. During the bar loop, BEFORE filling any pending order:
   - Check if today's date matches any corporate-action ex_date
   - If so, call `portfolio.apply_corporate_action(action)`:
     - BONUS 1:1: double quantity, halve avg_cost
     - SPLIT 2:1: double quantity, halve avg_cost
     - etc.
5. Signals are still computed on raw prices (SMA sees the discontinuity)

### What it solves

| Problem from impact review | Solved? | How |
|---|---|---|
| S1: SMA on raw close | ❌ | SMA still sees raw prices with discontinuity |
| S2: Spurious crossover | ❌ | The 50% price drop still triggers false signals |
| P3: Equity mark-to-market | ✅ | Shares doubled, so equity = 20 × 1,200 = correct |
| P1: No position adjustment | ✅ | Portfolio adjusts shares on ex-date |
| P2: Wrong avg_cost | ✅ | avg_cost halved to match raw post-bonus prices |
| M1: Phantom drawdown | ✅ | Equity curve is smooth through bonus |

### Critical gap

**Approach B fixes portfolio accounting but NOT signals.** The SMA crossover still computes on raw prices, so a false death cross fires after the bonus. The trades generated are wrong, even though the portfolio correctly tracks the adjusted position.

**Verdict: Approach B alone is also insufficient.** It solves the other half.

---

## Approach C -- Hybrid (RECOMMENDED)

```
Raw prices ──> Adjustment engine ──> Adjusted research series ──> Strategy (SMA)
                     |                                                  |
                     |                                               signals
                     |                                                  |
                     v                                                  v
              Corporate-action events ──> Execution simulator ──> Portfolio
                                          (adjusts positions               
                                           on ex-dates)                    
                                                  |
                                                  v
                                              Results
```

### How it works

**Two adjustments happen at two different layers:**

1. **Data layer (price adjustment):** Before the backtester runs, produce an adjusted price series. The SMA and all other indicators compute on this series. This prevents false signals.

2. **Execution layer (portfolio adjustment):** During simulation, when the ex-date is reached, adjust the portfolio's position (shares, avg_cost). This prevents phantom drawdowns and P&L errors.

**Key insight:** These two adjustments serve different purposes:
- Price adjustment serves **indicator correctness**
- Portfolio adjustment serves **accounting correctness**

Both are needed. Neither is redundant.

### What it solves

| Problem from impact review | Solved? | How |
|---|---|---|
| S1: SMA on raw close | ✅ | SMA computes on adjusted series |
| S2: Spurious crossover | ✅ | Adjusted series is continuous |
| P3: Equity mark-to-market | ✅ | Shares adjusted on ex-date |
| P1: No position adjustment | ✅ | `portfolio.apply_corporate_action()` |
| P2: Wrong avg_cost | ✅ | avg_cost adjusted on ex-date |
| M1: Phantom drawdown | ✅ | Both equity and prices are consistent |
| M2: Daily returns distorted | ✅ | Adjusted prices + adjusted shares = smooth curve |

---

## Evaluation Matrix

| Criterion | A: Adjusted Prices | B: Event-Driven | C: Hybrid |
|---|---|---|---|
| **Signal correctness** | ✅ Continuous prices | ❌ False crossovers | ✅ Continuous prices |
| **Portfolio accounting** | ❌ Wrong shares/avg_cost | ✅ Correct position | ✅ Correct position |
| **Equity curve** | ❌ Wrong (10 × adj vs 20 × adj) | ✅ Correct (20 × raw) | ✅ Correct |
| **P&L correctness** | ❌ Wrong quantity on sell | ✅ Correct | ✅ Correct |
| **Auditability** | ✅ Raw prices preserved | ✅ Raw prices used | ✅ Raw preserved, adjusted derived |
| **Reproducibility** | ✅ Deterministic | ✅ Deterministic | ✅ Deterministic |
| **Implementation complexity** | LOW (new data module) | MEDIUM (modify simulator) | MEDIUM (both) |
| **Testing complexity** | LOW (compare series) | MEDIUM (simulate events) | MEDIUM (both layers) |
| **Raw NSE compatibility** | ✅ Works with raw CSVs | ✅ Works with raw CSVs | ✅ Works with raw CSVs |
| **Future ML/features** | ✅ Adjusted features natural | ❌ ML sees discontinuities | ✅ Adjusted features natural |
| **Dividends** | ⚠️ Requires total-return series | ⚠️ Cash injection event | ⚠️ Both paths available |
| **Splits/bonuses** | ✅ Price adjustment | ✅ Share adjustment | ✅ Both |

**Recommendation: Approach C (Hybrid)**

---

## Detailed Design

### 1. Corporate Action Data Model

```python
@dataclass
class CorporateAction:
    """A single corporate action event for a security."""
    security_symbol: str
    action_type: str       # 'BONUS', 'SPLIT', 'SYMBOL_CHANGE', 'DIVIDEND', etc.
    ex_date: date
    record_date: date | None
    
    # For BONUS: ratio_new=1, ratio_existing=1 means 1:1 (one new per one held)
    # For SPLIT: ratio_new=2, ratio_existing=1 means 2-for-1
    ratio_new: int | None       # new shares issued
    ratio_existing: int | None  # per existing shares held
    
    # For DIVIDEND: amount per share
    dividend_per_share: float | None
    
    # For SYMBOL_CHANGE
    old_symbol: str | None
    new_symbol: str | None
```

### 2. Adjustment Factor Computation

```python
@dataclass
class AdjustmentFactor:
    """Multiplicative factor to apply to prices before a given date."""
    ex_date: date
    price_factor: float    # multiply prices by this (e.g., 0.5 for 1:1 bonus)
    volume_factor: float   # multiply volumes by this (e.g., 2.0 for 1:1 bonus)
    share_factor: float    # multiply share count by this (e.g., 2.0 for 1:1 bonus)
```

**Factor computation rules:**

| Action | price_factor | volume_factor | share_factor |
|---|---|---|---|
| BONUS 1:1 | 1/(1+1) = 0.5 | (1+1)/1 = 2.0 | 2.0 |
| BONUS 1:2 | 2/(2+1) = 0.667 | (2+1)/2 = 1.5 | 1.5 |
| BONUS 3:1 | 1/(1+3) = 0.25 | (1+3)/1 = 4.0 | 4.0 |
| SPLIT 2:1 | 1/2 = 0.5 | 2/1 = 2.0 | 2.0 |
| SPLIT 5:1 | 1/5 = 0.2 | 5/1 = 5.0 | 5.0 |
| SPLIT 1:2 (reverse) | 2/1 = 2.0 | 1/2 = 0.5 | 0.5 |

**General formula for BONUS ratio N:E (N new per E existing):**
```
price_factor = E / (E + N)
volume_factor = (E + N) / E
share_factor = (E + N) / E
```

**General formula for SPLIT ratio N:E (N shares for every E):**
```
price_factor = E / N
volume_factor = N / E
share_factor = N / E
```

### 3. Data Layer -- Price Adjustment Module

New file: `backtesting/core/adjustment.py`

```python
class PriceAdjuster:
    """
    Produces a research-adjusted price series from raw prices
    and corporate actions.
    
    Adjustment is applied backward from the most recent action:
    all prices BEFORE each ex_date are multiplied by the cumulative
    adjustment factor.
    
    The raw series is never modified. A new DataFrame is returned.
    """
    
    def adjust(
        self,
        raw_prices: pd.DataFrame,
        actions: list[CorporateAction],
    ) -> pd.DataFrame:
        """
        Returns adjusted DataFrame with same columns as raw_prices.
        
        Prices are adjusted; an 'adjustment_factor' column is added
        for auditability (shows the cumulative factor at each bar).
        """
```

**Key properties:**
- Raw DataFrame is NEVER mutated (copy returned)
- Cumulative factor: if two bonuses occur, factors multiply
- Adjustment applied backward from most recent to oldest
- An `adjustment_factor` column is added so any bar's adjusted price can be verified: `adjusted_close = raw_close * adjustment_factor`

### 4. Execution Layer -- Portfolio Adjustment

New method on `Portfolio`:

```python
def apply_corporate_action(
    self,
    symbol: str,
    action: CorporateAction,
    factor: AdjustmentFactor,
) -> None:
    """
    Adjust position for a corporate action.
    
    For bonus/split: multiply quantity by share_factor,
    divide avg_cost by share_factor. No cash change.
    No trade log entry (this is not a trade).
    
    Conservation invariant:
      pre_qty * pre_avg_cost == post_qty * post_avg_cost
    """
```

Modified `ExecutionSimulator.run()`:

```python
def run(self, signal_df, symbol, corporate_actions=None):
    # ...
    for i in range(len(df)):
        bar_date = row["trading_date"]
        
        # ---- Apply corporate actions on ex-date ----
        if corporate_actions:
            for ca in corporate_actions:
                if bar_date == ca.ex_date and symbol in portfolio.positions:
                    factor = compute_adjustment_factor(ca)
                    portfolio.apply_corporate_action(symbol, ca, factor)
        
        # ---- Fill pending orders (AFTER position adjustment) ----
        # ... (existing logic)
```

**Order of operations on ex-date:**
1. Adjust position (shares, avg_cost)
2. Fill any pending orders (at post-adjustment open price)
3. Record equity (with adjusted shares × post-adjustment close)

This order matters: if a SELL was pending from the previous day, the SELL should execute the adjusted share count at the post-adjustment price.

### 5. Data Source Changes

`CsvDataSource` and `DataFrameSource` get a new optional field:

```python
class CsvDataSource(DataInterface):
    def load(self, symbol, start, end) -> pd.DataFrame:
        # ... existing loading ...
        # New: return an 'adjustment_status' attribute
        # 'raw', 'adjusted', or 'unknown'
```

And a new data source for corporate actions:

```python
class CsvCorporateActionSource:
    """Load corporate actions from a CSV file."""
    
    def load(self, symbol: str) -> list[CorporateAction]:
        """Return all corporate actions for a symbol, sorted by ex_date."""
```

### 6. Engine Changes

`run_backtest()` gains a `corporate_actions` parameter:

```python
def run_backtest(
    data_source,
    signal_generator,
    symbol,
    corporate_actions: list[CorporateAction] | None = None,
    # ... existing params ...
):
    # 1. Load raw data
    raw_data = data_source.load(symbol, start, end)
    
    # 2. If corporate actions provided, adjust prices for indicators
    if corporate_actions:
        adjuster = PriceAdjuster()
        adjusted_data = adjuster.adjust(raw_data, corporate_actions)
    else:
        adjusted_data = raw_data
    
    # 3. Generate signals on ADJUSTED data
    signal_df = signal_generator.generate(adjusted_data)
    
    # 4. Simulate on ADJUSTED data, with portfolio adjustments
    simulator = ExecutionSimulator(...)
    portfolio = simulator.run(signal_df, symbol, corporate_actions)
    
    # 5. Compute metrics
    # ...
```

---

## What About Prices in the Trade Log?

A design decision:

**Option 1:** Trade log records ADJUSTED prices.
- Pro: P&L arithmetic is simple (sell_adj - buy_adj) * qty
- Con: Reported prices don't match what actually traded on the exchange

**Option 2:** Trade log records RAW prices, with adjustment metadata.
- Pro: Auditable against actual exchange data
- Con: P&L arithmetic must account for adjustment factor

**Recommendation:** Option 1 — record adjusted prices in the trade log. Reasoning:
- The backtester is a research tool, not an accounting system
- Adjusted prices make P&L computation straightforward
- The raw prices are always available in `daily_prices` / the original CSV
- The `adjustment_factor` column in the price data provides the mapping

---

## Double-Adjustment Prevention

**Risk:** If the data from yfinance is ALREADY adjusted AND we apply our own adjustment, prices are adjusted twice.

**Safeguard:**

1. The data source must declare its adjustment status: `raw`, `adjusted`, or `unknown`
2. The adjustment engine refuses to adjust data that is already marked `adjusted`
3. For `unknown` data, log a warning and proceed as if `raw` (but flag in results)
4. The experiment results JSON records: `"price_adjustment": "applied"` or `"none"` or `"source_pre_adjusted"`

**For yfinance specifically:** Our fetcher uses `auto_adjust=False`, which SHOULD give raw prices. But this needs explicit verification per the impact review (issue F1). A validation test should compare yfinance output against a known NSE bhavcopy for the same date.

---

## Version Control of Adjusted Datasets

**Rule:** Adjusted datasets are DERIVED, not source data.

```
data/raw/RELIANCE_2021-09-27_2026-09-24.csv          <- SOURCE (never modified)
data/corporate_actions/RELIANCE_actions.csv            <- SOURCE (curated manually)
data/adjusted/RELIANCE_2021-09-27_2026-09-24_v1.csv   <- DERIVED (regenerated)
```

Adjusted datasets can always be regenerated from raw + corporate actions. They don't need independent versioning — version the inputs instead.

---

## Implementation Plan

### Phase 1: Data Model + Adjustment Engine (no backtester changes)

- [ ] `CorporateAction` and `AdjustmentFactor` dataclasses
- [ ] `PriceAdjuster.adjust()` -- produces adjusted DataFrame
- [ ] `CsvCorporateActionSource` -- loads actions from CSV
- [ ] Unit tests: adjustment factor computation for all bonus/split ratios
- [ ] Unit tests: cumulative adjustment across multiple actions

### Phase 2: Portfolio Adjustment (execution changes)

- [ ] `Portfolio.apply_corporate_action()` method
- [ ] Conservation invariant enforced: `pre_qty * pre_avg_cost == post_qty * post_avg_cost`
- [ ] `ExecutionSimulator.run()` accepts corporate_actions, applies on ex-date
- [ ] Order-of-operations test: adjustment before fill on ex-date

### Phase 3: Engine Wiring

- [ ] `run_backtest()` accepts corporate_actions
- [ ] Adjustment applied to price data before signal generation
- [ ] Corporate actions passed to simulator for portfolio adjustment
- [ ] Double-adjustment safeguard
- [ ] Results JSON records adjustment metadata

### Phase 4: Regression Tests (from Bharath's test matrix)

- [ ] Test 1: Conservation -- 1:1 bonus
- [ ] Test 2: Conservation -- 2:1 split
- [ ] Test 3: No phantom SMA death cross
- [ ] Test 4: No phantom drawdown
- [ ] Test 5: P&L correctness through bonus
- [ ] Test 6: avg_cost adjustment precision (5 parametrized scenarios)
- [ ] Unsupported action halts: rights issue, merger, demerger

---

## Open Questions for Team Lead

1. **Fill price basis:** Should the execution simulator fill orders at adjusted prices (consistent with indicators) or raw prices (consistent with actual exchange)? I recommend adjusted, but this changes what the trade log reports.

2. **Corporate-action data source for V1:** Should we manually curate a CSV from NSE announcements, or attempt to parse from yfinance's `actions` DataFrame? Manual curation is more reliable but doesn't scale.

3. **Existing experiments:** M0.2.1 and M0.2.2 used potentially-adjusted yfinance data (the `auto_adjust=False` behavior is ambiguous). After implementing M0.2.3, should we re-run those experiments with verified-raw data and compare against the frozen results?

---

## Answers to Team Lead's 10 Questions

| # | Question | Answer |
|---|---|---|
| 1 | What corporate actions can affect an NSE equity price series? | Splits, bonus issues, rights issues, dividends, mergers/demergers, symbol changes, buybacks |
| 2 | Which require historical price adjustment? | Splits and bonuses (mandatory). Dividends (optional, for total-return). Rights (complex, deferred). |
| 3 | Where can we obtain authoritative data? | NSE corporate actions page, BSE corporate filings, company announcements. For V1: manual CSV curation from NSE. |
| 4 | How associate action with security_id? | Via `securities.symbol` + `securities.exchange`. The `corporate_actions` table already has `security_id` FK. |
| 5 | What is effective/ex-date? | Ex-date = first trading day when shares trade without entitlement. Price adjusts on ex-date open. |
| 6 | How are adjustment factors calculated? | BONUS N:E: price_factor = E/(E+N). SPLIT N:E: price_factor = E/N. Applied multiplicatively backward from ex-date. |
| 7 | How prevent double-adjustment? | Data source declares `raw`/`adjusted`/`unknown`. Adjustment engine refuses to adjust `adjusted` data. |
| 8 | How preserve original raw series? | `daily_prices` table / `data/raw/` CSVs are NEVER modified. Adjusted series are DERIVED in separate output. |
| 9 | How version adjusted datasets? | Version the inputs (raw prices + corporate-action CSV). Adjusted output is regenerated, not independently versioned. |
| 10 | How test adjustment correctness? | Bharath's test matrix: 6 tests, 10 scenarios. Conservation invariant is the anchor. Cross-verify against a known adjusted source (e.g., NSE adjusted close or a trusted vendor). |
