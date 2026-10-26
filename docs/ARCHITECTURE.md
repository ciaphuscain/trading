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
| Orders, fills (paper) | `OrderManager` | SQLite (write-ahead) |
| Experiments, holdout accesses, decisions | `Registry` | append-only hash-chained JSONL |

## Price-basis discipline
`PriceFrame.basis` is RAW or ADJUSTED and `MarketData.__post_init__` asserts it. The engine reads only
`data.raw`; strategies read `data.adj` for signals and raw close only for affordability. Adjustment is
*forward* (point-in-time), so truncating the data never changes past adjusted values
(`test_adjusted_prices_are_point_in_time`).

## Two execution paths, one set of rules
The backtester fills orders directly (fast, deterministic). The paper path goes through
`OrderManager` → `PaperBroker` (state machine, idempotency, reconciliation). Both use the same
`CostModel`, `RiskEngine` and `allocate`. Divergence between them is a known risk; Phase 5's daily
paper job will log model-vs-paper fill differences (see REQUIREMENTS P1-11).

## Evolution plan
| Version | Trigger to move on | New complexity |
|---|---|---|
| V0 (done) minimal backtester | – | – |
| V1 (this repo) reproducible multi-strategy research | real data loaded | – |
| V2 cost-aware optimisation + robust validation | a candidate passes the gate on real data | integer allocation, regime filters |
| V3 paper-trading daily job | V2 candidate exists | scheduler entry, state DB, alerts |
| V4 research orchestration / model registry | ML shows incremental OOS value | MLflow or equivalent |
| V5 multi-market/broker | separate legal + operational review | adapters, FX, calendars |
