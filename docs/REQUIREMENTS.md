# Requirements and priorities

P0 essential (correctness, validity, safety) · P1 high leverage · P2 after foundation · P3 advanced ·
P4 experimental · REJECT. ✔ = implemented and tested in this repository.

| ID | Requirement | Pri | Status | Justification |
|---|---|---|---|---|
| P0-1 | Canonical data model, RAW vs ADJUSTED separation, point-in-time adjustment | P0 | ✔ | look-ahead and price-mixing are the most common silent backtest bugs |
| P0-2 | Deterministic data validation, dataset version hash | P0 | ✔ | reproducibility; garbage-in detection |
| P0-3 | Dated, sourced, configurable cost schedule incl. DP per scrip-day, STT rounding | P0 | ✔ | costs decide feasibility at ₹5k |
| P0-4 | Reference backtester: next-open fills, whole shares, cash-only, slippage, participation, gaps, splits, dividends, delistings | P0 | ✔ | correctness over speed |
| P0-5 | Ledger invariants + property tests | P0 | ✔ | no unexplained cash, no fractional/unowned shares |
| P0-6 | Look-ahead tests (truncation invariance, strict-PIT equivalence) | P0 | ✔ | research validity |
| P0-7 | Cash and buy-and-hold benchmarks; ability to conclude "don't trade" | P0 | ✔ | the null hypothesis |
| P0-8 | Independent risk engine able to veto all orders; kill switch | P0 | ✔ | safety |
| P0-9 | Experiment registry recording every trial, tamper-evident | P0 | ✔ | multiple-testing honesty |
| P0-10 | Locked holdout with audited access | P0 | ✔ | prevents silent contamination |
| P0-11 | Walk-forward OOS, DSR, PBO, bootstrap CI, promotion gate | P0 | ✔ | overfitting defence |
| P0-12 | Order state machine, idempotency, UNKNOWN state, reconciliation, no-live guarantee | P0 | ✔ | duplicate/ambiguous orders |
| P1-1 | Real NSE data ingestion: bhavcopy downloader + corporate-action table + ISIN mapping | P1 | parser ✔, downloader ✔ (fake-fetcher tested; live endpoint unverified), actions table ☐, ISIN ☐ | nothing can be concluded without it |
| P1-2 | Parameter sensitivity tables / plateau check | P1 | ✔ (per-parameter marginal Sharpe in every report) | stability evidence |
| P1-3 | Regime-split performance tables (trend/vol regimes) | P1 | ✔ (causal threshold regimes, OOS breakdown in report) | robustness |
| P1-4 | Daily paper-trading job (Task Scheduler) using OrderManager + PaperBroker + SQLite | P1 | ✔ `omega paper`, `scripts/paper_daily.ps1` | Phase 5 |
| P1-5 | Model-vs-paper fill divergence log | P1 | ✔ per fill (`divergence_bps`); meaningful only with a real broker | validate slippage assumptions |
| P1-6 | CI on Windows + Linux | P1 | ✔ (observed passing on GitHub Actions) | Windows is the target OS |
| P1-7 | Nifty 50 TRI benchmark ingestion | P1 | loader ✔ (`benchmark.csv`) | required comparison |
| P2-1 | Cost-aware integer portfolio optimisation (vs equal-weight) | P2 | ☐ | only 1–2 slots; benefit unproven |
| P2-2 | Regime classifier (thresholds first) | P2 | trend gate ✔ | add only with demonstrated value |
| P2-3 | Parquet storage for large universes | P2 | ☐ (pyarrow optional) | CSV fine at current scale |
| P2-4 | Dashboard (Streamlit) | P2 | ☐ | reports in Markdown suffice now |
| P3-1 | LightGBM/XGBoost meta-labeling with purged CV | P3 | ☐ | only after a Tier-1 edge exists |
| P3-2 | Factor portfolios (quality, value) | P3 | ☐ | needs fundamentals data |
| P4-1 | HMM / Bayesian regimes, RL, transformers, GP strategy search | P4 | ☐ | no evidence simpler methods are inadequate |
| REJECT-1 | Kubernetes, Kafka, Redis, microservices | REJECT | – | no measured requirement; ops burden |
| REJECT-2 | Market making / order-book prediction (Avellaneda–Stoikov) | REJECT (for ₹5k EOD) | – | needs co-location-grade data and capital |
| REJECT-3 | Intraday strategies | REJECT (v0) | – | intraday costs + DP-free but higher STT/brokerage frequency; data unavailable |
| REJECT-4 | Paid broker API subscription for ₹5k | REJECT unless free | – | fee can exceed expected profit |
