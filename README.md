# OMEGA QUANT ∞

Evidence-first quantitative research and **paper-trading** platform for a ₹5,000 NSE cash-equity account.

> **Status:** research foundation built and tested. **No real-market result exists yet**: real NSE data was
> unreachable from the build environment, so every measured number in this repository is on clearly-labelled
> synthetic data and validates the *machinery*, not profitability. Live trading is **not implemented**
> (and cannot be enabled by configuration).

## What it does
* Ingests EOD bars (generic CSV or NSE bhavcopy), validates them, versions them by content hash.
* Backtests long-only, whole-share, cash-only strategies with next-open execution, slippage, liquidity caps,
  gap-aware stops, splits/dividends/delistings and a **dated NSE delivery charge schedule** (STT, exchange,
  SEBI, stamp, GST, **DP per scrip per sell day**).
* Runs a tournament of Tier-1 strategies (cross-sectional momentum, time-series trend, conditional mean
  reversion, Donchian breakout) against cash and buy-and-hold with walk-forward OOS selection, a locked
  holdout, a tamper-evident trial registry, Deflated Sharpe Ratio, PBO (CSCV), bootstrap CIs and a mechanical
  promotion gate whose default answer is **"hold cash"**.
* Runs a restartable **daily paper-trading job** (`omega paper`) through an order state machine, idempotent
  write-ahead order manager, simulated broker, reconciliation and an independent risk engine with kill switch.
  It uses the backtester's exact decision code and is tested to reproduce the backtest equity curve to the paisa.

## Key findings so far (synthetic data — see [docs/EXPERIMENT_LOG.md](docs/EXPERIMENT_LOG.md))
* A ₹2,500 delivery round trip costs ≈ ₹22.5 (0.90%), mostly the DP charge (hand-checked in tests).
* Charges consume 2.5%/yr (monthly momentum) to 18%/yr (short-term mean reversion) of a ₹5k account.
* On markets with **no** edge, 1–2-stock strategies produced >100% walk-forward OOS returns by luck; the
  trial-aware gate rejected all of them (0/16). On markets with a strong planted edge it promoted 4/16 — a
  deliberately strict, low-power gate.

## Quick start (Windows PowerShell)
```powershell
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock   # exact, hash-verified (Python 3.13)
python -m pip install --no-deps -e .
python -m pytest -q
python -m omega costs --value 2500
python -m omega validate   --synthetic
python -m omega backtest   --synthetic --strategy M1_xs_momentum --param lookback=126
python -m omega tournament --synthetic --out results\demo          # ~25 s
python -m omega tournament --data path\to\data --holdout-start 2024-01-01 --registry runs\registry.jsonl
python -m omega registry   --registry runs\registry.jsonl
python -m omega paper      --synthetic --strategy M1_xs_momentum --state runs\paper_demo   # daily paper job
python -m omega fetch-nse  --start 2015-01-01 --cache data_store\bhavcopy                  # read NSE terms first
python -m omega build-bars --cache data_store\bhavcopy --out data_store\nse                 # ISIN-continuous symbols
python -m omega parse-actions --input nse_corporate_actions.csv --out data_store\nse\actions.csv
```
Data directory format: `bars.csv` (date,symbol,open,high,low,close,volume), optional `actions.csv`
(symbol,ex_date,kind∈{split,dividend},value), optional `benchmark.csv` (date,close).

## Layout
```
omega/data         canonical model, validation, sources (CSV, bhavcopy, synthetic)
omega/costs        dated charge schedule engine + slippage model   (config: omega/config/*.yaml)
omega/strategies   indicators, strategy interface, Tier-1 library
omega/portfolio    ledger (invariants), whole-share cost-aware allocator
omega/risk         independent pre-trade risk engine
omega/backtest     reference event-driven engine
omega/stats        metrics, PSR/DSR, PBO, bootstrap, multiple testing
omega/research     registry, holdout lock, walk-forward, tournament, promotion gate
omega/execution    order state machine, order manager, paper broker, live guard
omega/paper        daily paper-trading session (restartable, crash-safe)
tests/             unit, property, look-ahead, integration, failure-injection tests
scripts/           reproducible experiments
docs/              specification, methods, assumptions, ADRs, logs
```

## Status
| Phase | State |
|---|---|
| 0 Feasibility | ✔ docs: [PROJECT_SPEC](docs/PROJECT_SPEC.md), [ASSUMPTIONS](docs/ASSUMPTIONS.md), [DATA_SOURCES](docs/DATA_SOURCES.md), [RESEARCH_METHODS](docs/RESEARCH_METHODS.md) |
| 1 Research foundation | ✔ data model, validation, cost engine, reference backtester, invariants, baselines |
| 2 Strategy tournament | ✔ M1/M2/R1/B1, walk-forward, parameter-sensitivity and regime tables, registry — **on synthetic data only** |
| 3 Optimisation | partial: cost-aware filter, whole-share allocator, risk budgets; regime gate in M1; integer optimiser deliberately not built (no evidence it helps with 1–2 slots) |
| 4 Machine learning | not started — not justified until a Tier-1 edge exists on real data |
| 5 Paper trading | ✔ daily job (`omega paper`, `scripts/paper_daily.ps1`), reconciliation, risk, fill-divergence log |
| 6 Hardening | ✔ CI (Windows + Linux, pip-audit), failure injection incl. crash, disk-full, DB lock, corruption, backups |
| 7 Live | **not started by design** (ADR 0006) |

Next step (needs the operator): download real data with `omega fetch-nse` from a machine where NSE is
reachable, convert NSE's corporate-actions export with `omega parse-actions` (review the unparsed rows), decide the open protocol question in EXPERIMENT_LOG (trade-count
rule) **before** looking at real results, then run the tournament with a locked holdout.

Documentation index: [ARCHITECTURE](docs/ARCHITECTURE.md) · [STRATEGIES](docs/STRATEGIES.md) ·
[BACKTEST_ASSUMPTIONS](docs/BACKTEST_ASSUMPTIONS.md) · [TRANSACTION_COSTS](docs/TRANSACTION_COSTS.md) ·
[RISK_MODEL](docs/RISK_MODEL.md) · [SECURITY](docs/SECURITY.md) · [TESTING](docs/TESTING.md) ·
[BENCHMARKS](docs/BENCHMARKS.md) · [REQUIREMENTS](docs/REQUIREMENTS.md) · [OPERATIONS](docs/OPERATIONS.md) ·
[DEPLOYMENT](docs/DEPLOYMENT.md) · [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) ·
[QUALITY_GATES](docs/QUALITY_GATES.md) · [INTERVIEW_QUESTIONS](docs/INTERVIEW_QUESTIONS.md) ·
[EXPERIMENT_LOG](docs/EXPERIMENT_LOG.md) · [ADRs](docs/adr/)

Not financial advice. Past (let alone synthetic) performance does not predict future results.
