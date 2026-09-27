# M0.2.4 — Benchmark Framework Design (Revised)

**Author:** Vishnu  
**Date:** 2026-09-27  
**Status:** DESIGN — awaiting Team Lead approval before implementation  
**Depends on:** M0.2.3 (CLOSED), M0.2-I1 (CLOSED)

---

## Purpose

M0.2.4 adds a benchmark layer that allows any strategy to be compared against
Buy & Hold under controlled, identical conditions. This is the **minimum
requirement** before any claim of alpha can be evaluated.

The benchmark framework is a **consumer** of the existing backtesting engine.
It does not rewrite the engine.

---

## Architecture

```
                 BenchmarkConfig
                       │
                       ▼
                BenchmarkRunner
                 /            \
                /              \
               ▼                ▼
        Buy & Hold          Strategy
        (BuyAndHoldStrategy)  (SMAStrategy)
             │                  │
             └──────┬───────────┘
                    ▼
             BacktestResult (×2)
                    │
                    ▼
             BenchmarkReport
              /      |       \
             ▼       ▼        ▼
          Metrics   Alpha    Config
```

---

## Controlled Conditions

Both strategy and benchmark are subject to the same:

| Parameter | Value | Rationale |
|---|---|---|
| Security | Explicitly specified (e.g. RELIANCE/EQ) | No implicit universe |
| Start date | Explicitly specified before experiment | Frozen before results inspection |
| End date | Explicitly specified before experiment | Frozen before results inspection |
| Initial capital | Same (e.g. ₹1,000,000) | Comparable base |
| Cost model | Same (e.g. 0.1% round-trip) | No cost advantage |
| Trade sizing rule | Same `trade_fraction` (e.g. 0.95) | Same sizing convention |
| Price series | Same adjusted research series | Same corporate-action treatment |
| Corporate actions | Same event log version | Same portfolio accounting |
| Dataset snapshot | Same version/hash | Provenance equality |

### Correction 1: Exposure is observed, not controlled

Both strategies use the same trade sizing rule (`trade_fraction=0.95`), while
**realized exposure remains an observed metric rather than a controlled
equality**.

Buy & Hold deploys capital once and remains invested for the full period.
SMA enters and exits multiple times. Therefore their average exposure,
turnover, cash drag, and realized risk **will differ substantially**. These
differences are part of what we measure, not something we equalize away.

Metrics that capture this:

| Metric | Buy & Hold | SMA |
|---|---|---|
| Average exposure | ~95% (constant) | Variable |
| Turnover | 1 trade | N trades |
| Cash drag | Minimal | Depends on signal timing |
| Max drawdown | Full market drawdown | May differ |
| Time in market | ~100% | < 100% |

**Rule:** The report must present both `time_in_market` and `average_exposure`
so that alpha claims are contextualized.

### Correction 2: Evaluation period explicitly frozen before experiment

The evaluation period **must be explicitly specified before the experiment
runs**. Data availability determines what periods are possible, but
availability must NOT determine the evaluation period after results are
inspected.

```
PROCEDURE:
  1. Query DataInterface for available date range
  2. SELECT a period (explicitly, with justification)
  3. FREEZE the period in BenchmarkConfig
  4. Run both strategy and benchmark on frozen period
  5. Report results

NEVER:
  - Run on "full available range" and then cherry-pick
  - Change period after seeing results
  - Let the CSV determine the benchmark boundaries
```

For the first experiment, the available RELIANCE data will be queried, a
period will be selected, and once selected it is **locked in
BenchmarkConfig** before any strategy run.

### Correction 3: Corporate-action contract (Hybrid C preserved)

M0.2.3 established a hybrid architecture. M0.2.4 must explicitly preserve it:

> **Signals are generated from the adjusted research series, while portfolio
> accounting remains consistent with the validated corporate-action/event
> model. Raw NSE prices remain immutable.**

```
Raw NSE prices (immutable, NEVER touched)
         │
Corporate Action Event Log (versioned)
         │
    ┌────┴─────┐
    │          │
Adjusted       Event-driven
Research       Portfolio
Series         Accounting
    │              │
    ▼              ▼
SMA/Indicators   Shares, Cost,
Buy signals      Cash flows
    │              │
    └──────┬───────┘
           ▼
    BacktestResult
```

**Anti-pattern we must prevent:**

```
adjusted prices → second adjustment → DOUBLE ADJUSTMENT (WRONG)
```

**Rule:** The benchmark layer receives already-adjusted prices from
DataInterface and passes them directly to the engine. It must NOT apply
any additional price transformation.

**Implementation guard:**

```python
class BenchmarkRunner:
    def run(self, config: BenchmarkConfig) -> BenchmarkReport:
        # Load data ONCE through DataInterface
        data = self.data_interface.load_market_data(
            symbol=config.symbol,
            series=config.series,
            start_date=config.start_date,
            end_date=config.end_date,
            adjusted=config.adjusted,
            adjustment_scope=config.adjustment_scope,
        )
        events = self.data_interface.load_corporate_events(...)

        # SAME data fed to BOTH strategies
        bh_result = buy_and_hold.run(data, events=events)
        strategy_result = strategy.run(data, events=events)

        # Data is loaded ONCE, used TWICE — no double adjustment possible
```

---

## BenchmarkConfig

```python
@dataclass(frozen=True)
class BenchmarkConfig:
    """Immutable benchmark configuration. Frozen before any run."""

    # Security
    symbol: str
    series: str = "EQ"

    # Evaluation period (explicitly specified, NOT derived from data)
    start_date: date
    end_date: date

    # Capital
    initial_capital: float = 1_000_000.0

    # Sizing
    trade_fraction: float = 0.95

    # Cost model
    cost_per_trade_pct: float = 0.001  # 0.1% round-trip

    # Corporate action treatment
    adjusted: bool = True
    adjustment_scope: str = "SPLIT_BONUS"

    # Strategy parameters (for the strategy under test)
    strategy_params: dict = field(default_factory=dict)

    # Provenance
    dataset_version: str = ""  # hash or version tag
    config_version: str = "1.0"
```

**`frozen=True`** ensures the config cannot be modified after creation.

---

## BuyAndHoldStrategy

```python
class BuyAndHoldStrategy:
    """Buy once on day 1, hold until the end.

    Invariants:
        - Exactly 1 BUY signal (on the first valid bar)
        - 0 SELL signals during the holding period
        - No repeated entries
        - Position held until final bar
    """

    def run(self, df, initial_capital, trade_fraction, cost_pct, events=None):
        # Buy on bar 0
        # Hold until bar N
        # Apply corporate action events (same as SMA)
        # Report single trade
```

---

## BenchmarkReport

```python
@dataclass
class BenchmarkReport:
    """Complete benchmark comparison output."""

    config: BenchmarkConfig

    # Results
    strategy_result: BacktestResult
    benchmark_result: BacktestResult  # Buy & Hold

    # Derived metrics
    strategy_total_return: float
    benchmark_total_return: float
    alpha: float  # strategy - benchmark (arithmetic)
    alpha_pct: float

    # Exposure metrics
    strategy_time_in_market: float  # fraction of days with position
    benchmark_time_in_market: float  # should be ~1.0
    strategy_avg_exposure: float
    benchmark_avg_exposure: float

    # Risk
    strategy_max_drawdown: float
    benchmark_max_drawdown: float
    strategy_trades: int
    benchmark_trades: int  # should be 1

    # Corporate action
    strategy_events_applied: int
    benchmark_events_applied: int
    strategy_dividend_income: float
    benchmark_dividend_income: float

    # Provenance
    dataset_version: str
    data_hash: str  # SHA256 of the input DataFrame
```

---

## Benchmark Invariants (Required Tests)

### 1. Buy & Hold invariants

```python
def test_buy_and_hold_exactly_one_buy():
    """Buy & Hold must produce exactly 1 BUY, 0 SELL signals, no re-entry."""
    result = buy_and_hold.run(data, ...)
    assert result.total_trades == 1
    assert result.trades[0].direction == "BUY"
    # No sell until end — position held to final bar

def test_buy_and_hold_no_intermediate_sells():
    """Buy & Hold must never generate a SELL during the holding period."""
    # The only exit is the synthetic close at the final bar
```

### 2. Same-data invariant

```python
def test_same_data_source():
    """Both runs must consume the exact same dataset snapshot."""
    report = runner.run(config)
    assert report.data_hash == hashlib.sha256(data.to_csv().encode()).hexdigest()
    # Both strategy and benchmark see the identical bytes

def test_same_dataset_version():
    """Dataset version/provenance must be recorded and identical."""
    assert report.dataset_version != ""
    # Provenance tracked in BenchmarkConfig
```

### 3. Determinism

```python
def test_deterministic_results():
    """Same config + same data → identical results."""
    r1 = runner.run(config)
    r2 = runner.run(config)

    assert r1.strategy_total_return == r2.strategy_total_return
    assert r1.benchmark_total_return == r2.benchmark_total_return
    assert r1.alpha == r2.alpha
    assert r1.strategy_result.total_trades == r2.strategy_result.total_trades
    # Trade-by-trade comparison
    for t1, t2 in zip(r1.strategy_result.trades, r2.strategy_result.trades):
        assert t1.entry_date == t2.entry_date
        assert t1.exit_date == t2.exit_date
        assert t1.entry_price == t2.entry_price
        assert t1.exit_price == t2.exit_price
```

### 4. Capital conservation

```python
def test_capital_conservation():
    """For any run: initial_capital = final_cash + position_value + total_costs.

    On a synthetic rising dataset with no costs, final value > initial.
    The conservation law is:
        initial = cash + market_value + cumulative_costs
    """
    result = strategy.run(synthetic_rising_data, ...)
    assert result.final_cash + result.final_position_value + result.total_costs == \
        pytest.approx(config.initial_capital + result.total_pnl)
```

### 5. Controlled-conditions enforcement

```python
def test_same_initial_capital():
    """Both runs use the same starting capital."""
    assert report.strategy_result.initial_capital == report.benchmark_result.initial_capital

def test_same_cost_model():
    """Both runs use the same cost per trade."""
    # Embedded in BenchmarkConfig, frozen=True

def test_same_trade_fraction():
    """Both runs use the same sizing rule."""
    # Embedded in BenchmarkConfig, frozen=True

def test_same_corporate_actions():
    """Both runs see the same CA event log."""
    assert report.strategy_events_applied >= 0
    assert report.benchmark_events_applied >= 0
    # Events come from the same DataInterface call

def test_same_date_range():
    """Both runs cover exactly the same date boundaries."""
    s = report.strategy_result
    b = report.benchmark_result
    assert s.start_date == b.start_date
    assert s.end_date == b.end_date
```

### 6. Alpha computation correctness

```python
def test_alpha_is_computed():
    """Alpha must be computed. It does NOT assert alpha > 0."""
    report = runner.run(config)
    assert report.alpha is not None
    assert report.alpha == report.strategy_total_return - report.benchmark_total_return

def test_alpha_does_not_assert_positive():
    """This is a research question, not a software invariant.
    A test must NOT say: assert strategy_beats_benchmark"""
    # Intentionally empty — this docstring IS the test
    pass
```

---

## Implementation Plan

### Files to create

| File | Purpose |
|---|---|
| `backtesting/benchmark.py` | BenchmarkConfig, BuyAndHoldStrategy, BenchmarkRunner, BenchmarkReport |
| `tests/test_benchmark.py` | All invariant tests listed above |

### Files to modify

| File | Change |
|---|---|
| `backtesting/engine.py` | Add `start_date`, `end_date` to BacktestResult if not present |
| `docs/architecture-backlog.md` | Record M0.2.4 decision |
| `docs/research-log.md` | Record benchmark methodology |

### Files NOT modified

| File | Reason |
|---|---|
| `ingestion/nse_source.py` | Data layer unchanged |
| `ingestion/corporate_actions.py` | Adjustment engine unchanged |
| `ingestion/data_interface.py` | API unchanged (already supports adjusted param) |
| `schema.sql` | No schema changes |

---

## First Experiment Protocol (after implementation approval)

```
1. Query RELIANCE/EQ available dates via DataInterface
2. Select evaluation period (Team Lead approves)
3. Freeze BenchmarkConfig
4. Run BenchmarkRunner
5. Report:
   - Buy & Hold return
   - SMA 50/200 return
   - Alpha (arithmetic)
   - Exposure metrics
   - Trade count
   - Max drawdown
   - Corporate action events applied
   - Dataset provenance
6. Team Lead reviews
7. M0.2.4 closure decision
```

**The 50/200 SMA parameters remain frozen (M0.2.1).**

---

## What this design does NOT do

- Does NOT optimize SMA parameters (that is a future milestone)
- Does NOT claim alpha exists (that is a research outcome)
- Does NOT modify raw prices (M0 rule preserved)
- Does NOT apply additional price adjustments (Hybrid C preserved)
- Does NOT let data availability determine the evaluation period
- Does NOT equalize exposure (exposure is observed, not controlled)

---

## Gate Criteria

| Requirement | Status |
|---|---|
| BenchmarkConfig frozen | Implementation |
| Explicit evaluation period | In design |
| Same dataset snapshot | In design |
| Same capital | In design |
| Same cost model | In design |
| Same sizing rule | In design |
| Same CA treatment | In design |
| Determinism tests | In design |
| B&H invariant tests | In design |
| Capital conservation tests | In design |
| BenchmarkRunner | Implementation |
| BenchmarkReport | Implementation |
| First NSE benchmark run | After implementation |
| Team Lead review | After first run |
