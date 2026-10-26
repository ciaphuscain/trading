# ADR 0004 — SQLite for orders, append-only JSONL for experiments, CSV/Parquet for data

* Status: accepted
* SQLite: transactional, file-based, zero-admin; write-ahead order persistence and crash recovery.
* JSONL with hash chain: human-readable, append-only, tamper-evident experiment history.
* Data files: CSV now (versioned by content hash); Parquet optional when universes grow.
* Rejected: PostgreSQL (admin burden without a concurrency need), Redis/Kafka (no streaming requirement).
* Migration trigger: concurrent writers or multi-machine operation.
