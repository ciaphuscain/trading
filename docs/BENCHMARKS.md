# Performance benchmarks

Measured in the build container (Linux, CPython 3.13, numpy 2.x, pandas 3.x; CPU-only, single process)
with `scripts/run_experiments.py --only 5` and ad-hoc re-measurement after optimisation. Synthetic data.
Numbers will differ on the operator's Windows laptop; re-run the script there.

## Backtest throughput (reference engine, invariants checked every session)
| Universe | Sessions | Strategy | Median wall time (3 runs) | Symbol-sessions/s | Peak traced memory |
|---|---|---|---|---|---|
| 30 | 1,500 | M1 momentum | 0.18 s | 251k | 3.1 MB |
| 30 | 1,500 | B1 breakout | 0.25 s | 182k | 5.3 MB |
| 100 | 2,500 | M1 momentum | 0.75 s → **0.33 s** after optimisation | 333k → 758k | 16 MB |
| 100 | 2,500 | B1 breakout | 0.78 s → **0.50 s** after optimisation | 319k → 500k | 29 MB |
| 200 | 2,500 | M1 momentum | 1.24 s (before optimisation) | 403k | 32 MB |
| 10 | 400 | M1 strict point-in-time reference | 1.47 s vs 0.040 s fast path (≈37× slower) | – | – |

A full default tournament (6 baselines + 68 grid configurations + walk-forward + PBO + bootstrap) on
30 symbols × 1,500 sessions took ≈ 19–26 s per run.

## Profile → optimisation log
1. `cProfile` on B1, 100×2,500 (1.49 s): engine loop self-time 0.55 s; `Ledger.check_invariants`
   0.37 s (25%); rolling median for liquidity 0.11 s; dataset hashing 0.08 s.
2. Change: the engine built a price dict for **every** symbol every session; now it builds one only for
   target + held symbols and skips allocation when the strategy returns "hold".
3. Verification: equity curves for all six strategies are identical before/after (sum of equity series
   compared on the 100×2,500 dataset), and the full test suite (including fast == strict-PIT) passes.
4. Not changed deliberately: per-session invariant checks (≈25% of runtime) stay on by default —
   correctness over speed. `BacktestConfig(check_invariants=False)` exists for large sweeps *after* a
   configuration has been validated with checks on (measured: 0.33 s → 0.26 s for M1).

## Conclusion
Research iteration speed is not a bottleneck at NSE-EOD scale (≈2,000 liquid symbols × 15 years would be
~ 1–2 minutes per configuration single-threaded). No Numba/vectorbt/parallelism is justified yet; the next
step if needed is process-level parallelism over grid configurations (embarrassingly parallel).
