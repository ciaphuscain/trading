# Quality gates — current self-assessment (no overall score by design)

| Dimension | Evidence now | Weaknesses | Required next |
|---|---|---|---|
| Correctness | hand-calculated trade/cost tests; ledger property tests; per-session invariants; strict-PIT equivalence; deterministic replay | circuit limits and intraday path not modelled | circuit-lock rule; more corporate-action types (rights, mergers) |
| Research validity | locked holdout, walk-forward, DSR with all trials, PBO, bootstrap, gate; null/planted experiments (E1/E2) | only synthetic data; switching costs at fold boundaries ignored | run on real survivorship-free NSE data |
| Profitability | **unmeasured on real data**; on synthetic data costs dominate high-turnover strategies (E3/E4) | – | real-data tournament |
| Risk | independent engine, tests for each rule, kill switch, drawdown cooldown | limits are hypotheses; no stress scenarios from real crashes | historical stress replay (2008, 2020) |
| Data quality | validation checks, content version hash, bhavcopy parser | no downloader; no ISIN mapping; holiday calendar inferred | P1-1 |
| Execution | state machine, idempotency, write-ahead, reconciliation, failure-injection tests | no daily paper job yet; no real broker contract test | Phase 5 job |
| Reliability | crash-recovery test | disk-full / DB-lock / backup not tested | Phase 6 |
| Security | threat model; no live path; safe YAML; secrets ignored | no dependency audit in CI | add `pip-audit` |
| Performance | measured benchmarks, profile-driven optimisation verified equal | single-threaded | parallel grid only if needed |
| Maintainability | small modules, typed dataclasses, docs per subsystem | – | keep docs in sync |
| Observability | events, risk log, fills/trades CSV, registry, report | no dashboard; paper metrics not yet live | Phase 5 |
| Cost efficiency | ₹0 running cost; no paid data/API | – | keep it that way unless evidence justifies |
| Portfolio quality | ADRs, reversed decisions documented (drawdown halt, DSR variance) | – | – |
