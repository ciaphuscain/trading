# ADR 0002 — Own small event-driven reference backtester

* Status: accepted
* Problem: NSE delivery economics at ₹5k depend on details (whole shares, DP fee per scrip per sell day,
  STT rupee rounding, gap fills, corporate actions) that generic frameworks do not model out of the box.
* Candidates: LEAN, vectorbt, Backtrader, custom.
* Decision: custom engine (~300 lines) with exhaustive tests; a vectorised screener may be added later
  but must be validated against this engine.
* Evidence: hand-calculated trade tests, ledger property tests, strict point-in-time equivalence test;
  throughput 0.2–0.8 M symbol-sessions/s (BENCHMARKS.md) is sufficient.
* Replace when: grid sizes make research slow (> minutes per family) — then add a fast screener, not replace.
