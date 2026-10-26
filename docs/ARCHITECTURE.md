# Architecture

Modular monolith (single Python package, single process). Rationale: [adr/0001](adr/0001-modular-monolith.md).

```
                ┌──────────────┐
  CSV/bhavcopy ─►  data/        │  MarketData (RAW and ADJUSTED PriceFrames, actions, version hash)
                │  validation   │
                └──────┬───────┘
                       │ read-only
        ┌──────────────┼──────────────────────────────┐
        ▼              ▼                              ▼
  strategies/     backtest/engine ──► portfolio/ledger (state: cash, positions, journal)
  (signals on     │   │   │
   ADJUSTED)      │   │   └──► costs/model (dated schedule YAML)
        ▲         │   └──────► risk/engine (independent; can veto everything)
        └─decide──┘──────────► portfolio/allocator (whole shares, cost-aware)
                       │
                       ▼
                stats/ (metrics, PSR/DSR, PBO, bootstrap)  ◄── research/ (registry, holdout,
                                                                walk-forward, tournament, gate)
  execution/ (orders state machine, OrderManager + SQLite journal, PaperBroker, live_guard)
  paper/session  daily job: PaperBroker + OrderManager + the SAME plan_orders as backtest/engine
        └── uses risk/ via a callback; never imported by strategies/
```

## Dependency direction (enforced by convention, checked in review)
`data` ← `strategies` ← `backtest` → (`portfolio`, `costs`, `risk`) ; `stats` ← `research` → `backtest`.
* `strategies` never import `backtest`, `risk`, `execution` or `costs`: a strategy cannot see or
  change risk limits, and cannot place orders.
* `risk` imports nothing from `strategies`.
* `execution` never imports `strategies`; it receives order requests.

## State ownership
| State | Owner | Persistence |
|---|---|---|
| Prices, actions | `MarketData` (immutable) | operator's CSV/Parquet files, identified by `version()` hash |
| Cash, positions, journal (backtest) | `Ledger` | in memory per run; exported CSV |
| Risk counters, drawdown halt | `RiskEngine.state` | in memory; audit JSONL optional |
| Orders, fills, position adjustments (paper) | `OrderManager` | SQLite (write-ahead) |
| Paper broker account (cash, positions, resting orders) | `PaperBroker` (plays an external system) | JSON file, atomically replaced after every mutation |
| Paper session (risk state, position metadata, daily log) | `PaperSession` | SQLite, one transaction per session |
| Experiments, holdout accesses, decisions | `Registry` | append-only hash-chained JSONL |

## Price-basis discipline
`PriceFrame.basis` is RAW or ADJUSTED and `MarketData.__post_init__` asserts it. The engine reads only
`data.raw`; strategies read `data.adj` for signals and raw close only for affordability. Adjustment is
*forward* (point-in-time), so truncating the data never changes past adjusted values
(`test_adjusted_prices_are_point_in_time`).

## Two execution paths, one set of rules
The backtester fills orders directly (fast, deterministic). The paper path (`omega/paper/session.py`)
goes through `OrderManager` → `PaperBroker` (state machine, idempotency, write-ahead persistence,
reconciliation). Both call the **same** `plan_orders` (strategy → allocator → risk engine), `CostModel`
and `SlippageModel`. `tests/test_paper.py` asserts that a day-by-day paper run reproduces the backtest
equity curve to 1e-6 INR for every strategy. Building it found three real defects — two in the
backtester, one in the paper path (ADR 0007).
Remaining intentional difference: the paper broker has no volume-participation cap (a real broker
fills what the market gives); each paper fill logs its model price and divergence in bps.

## Evolution plan
| Version | Trigger to move on | New complexity |
|---|---|---|
| V0 (done) minimal backtester | – | – |
| V1 (this repo) reproducible multi-strategy research | real data loaded | – |
| V2 cost-aware optimisation + robust validation | a candidate passes the gate on real data | integer allocation, regime filters |
| V3 paper-trading daily job (built: `omega paper`, `scripts/paper_daily.ps1`) | V2 candidate exists | scheduler entry, state DB, alerts |
| V4 research orchestration / model registry | ML shows incremental OOS value | MLflow or equivalent |
| V5 multi-market/broker | separate legal + operational review | adapters, FX, calendars |
