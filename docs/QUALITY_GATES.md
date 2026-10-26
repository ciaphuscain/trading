# Quality gates — current self-assessment (no overall score by design)

| Dimension | Evidence now | Weaknesses | Required next |
|---|---|---|---|
| Correctness | hand-calculated trade/cost tests; ledger property tests; per-session invariants; strict-PIT equivalence; deterministic replay | circuit limits and intraday path not modelled | circuit-lock rule; more corporate-action types (rights, mergers) |
| Research validity | locked holdout, walk-forward, DSR with all trials, PBO, bootstrap, gate; null/planted experiments (E1/E2) | only synthetic data; switching costs at fold boundaries ignored | run on real survivorship-free NSE data |
| Profitability | **unmeasured on real data**; on synthetic data costs dominate high-turnover strategies (E3/E4) | – | real-data tournament |
| Risk | independent engine, tests for each rule, kill switch, drawdown cooldown | limits are hypotheses; no stress scenarios from real crashes | historical stress replay (2008, 2020) |
| Data quality | validation checks, content version hash, bhavcopy parser and cached downloader | live endpoint unverified; no corporate-action table builder; no ISIN mapping; holiday calendar inferred | P1-1 remainder |
| Execution | state machine, idempotency, write-ahead, reconciliation, AMO timing, failure-injection tests; daily paper job reproduces backtest exactly | no real broker contract test | broker sandbox contract tests (only if live is ever considered) |
| Reliability | crash recovery (order path and paper session), disk-full/DB-lock, integrity check, backups | power-loss mid-file-write only covered by atomic replace | – |
| Security | threat model; no live path; safe YAML; secrets ignored; pip-audit in CI | no hash-pinned lock file | lock file for the operator machine |
| Performance | measured benchmarks, profile-driven optimisation verified equal | single-threaded | parallel grid only if needed |
| Maintainability | small modules, typed dataclasses, docs per subsystem | – | keep docs in sync |
| Observability | events, risk log, fills/trades CSV, registry, report with regime/sensitivity tables, paper daily log with fill divergence | no dashboard | only if Markdown reports prove insufficient |
| Cost efficiency | ₹0 running cost; no paid data/API | – | keep it that way unless evidence justifies |
| Portfolio quality | ADRs, reversed decisions documented (drawdown halt, DSR variance, buy-shrinking, AMO timing) | – | – |
