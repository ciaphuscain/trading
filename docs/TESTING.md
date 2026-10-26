# Testing

Run: `python -m pytest` (Windows PowerShell or Linux). Last run in the build environment: **81 passed in ~18 s**
(Linux, Python 3.13). GitHub Actions CI passed on windows-latest and ubuntu-latest with Python 3.11 and 3.13 (run 40253802229, commit 4ca7ef8).

| Layer | File | What it proves |
|---|---|---|
| Cost unit tests (hand-calculated) | `tests/test_costs.py` | exact ₹ charges for a ₹2,500 buy/sell, DP once per scrip-day, dated schedule switch, monotonicity (Hypothesis) |
| Ledger + property tests | `tests/test_ledger.py` | 300 random operation sequences never break cash/qty invariants; fractional/unowned/duplicate rejected; split cash-in-lieu |
| Backtest correctness | `tests/test_engine.py` | hand-calculated next-open trade; gap-through-stop; missing bar; split + dividend; insufficient cash; delisting; determinism; risk veto; fast == strict point-in-time for every strategy |
| Look-ahead | `tests/test_lookahead.py` | adjusted prices, 7 indicators and all strategies are truncation-invariant |
| Data quality | `tests/test_validation.py`, `tests/test_sources.py` | corrupted candle, duplicates, unadjusted split, non-positive price, future date; CSV round trip keeps version; bhavcopy layouts |
| Statistics | `tests/test_stats.py` | MDD/CVaR hand values; PSR; DSR penalises best-of-200 noise; bootstrap; PBO noise vs real edge; Holm/BH hand values; STCG |
| Research protocol | `tests/test_research.py` | registry tamper detection; holdout lock; walk-forward bookkeeping; gate; null-market tournament promotes nothing |
| Risk engine | `tests/test_risk.py` | each rule, kill switch file + manual with audit, drawdown cooldown/re-arm |
| Execution / failure injection | `tests/test_execution.py` | see table below |

## Failure injection
| Failure | Expected | Observed (test) | Recovery | Residual risk |
|---|---|---|---|---|
| Broker timeout after accept | order → UNKNOWN, no resubmit | ✔ `test_timeout_after_accept…` | reconcile finds FILLED | a broker without client tags cannot be queried by our id |
| Timeout before accept | UNKNOWN, then REJECTED after 2 not-found checks | ✔ | new decision needs new attempt key | broker latency longer than the checks |
| Process crash during submission | SUBMITTED persisted (write-ahead) | ✔ `test_crash_after_write_ahead…` | restart → reconcile → FILLED, duplicate blocked | DB file loss |
| Duplicate submit of same decision | blocked by idempotency key | ✔ | – | – |
| Partial fill / duplicate fill reports | quantities applied once | ✔ | – | – |
| Position break vs broker | manager halts new orders | ✔ | human review | – |
| Corrupted candle / duplicate rows | validation ERROR | ✔ | fix data | subtle errors within bounds |
| Missing session for a symbol | order rejected | ✔ | strategy re-decides | – |
| Stale data | risk rejects | ✔ | – | – |
| Sudden gap through stop | fill at open | ✔ | – | gaps larger than modelled slippage |
| Fee change | new dated schedule row | ✔ dated lookup test | – | operator must update YAML |
| Disk full / DB lock / model corruption | not yet tested | ☐ | – | open item (Phase 6) |
