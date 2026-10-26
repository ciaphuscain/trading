# ADR 0001 — Modular monolith

* Status: accepted
* Problem: structure a one-operator research + paper-trading system with minimal operational burden.
* Candidates: (A) single-process modular package; (B) A + scheduled worker; (C) distributed services.
* Decision: A now; B (one Windows Task Scheduler entry) when the daily paper job is built.
* Rationale: no throughput or availability requirement that a single process cannot meet (BENCHMARKS.md);
  every network hop adds failure modes (timeouts, partial failure) that would need their own tests.
* Cost: ₹0. Failure modes: a crash stops everything — acceptable for EOD trading; state is persisted
  (SQLite order journal, JSONL registry) and reconciled on restart.
* Migration trigger: a measured need such as multiple concurrent brokers/markets or intraday data volume.
