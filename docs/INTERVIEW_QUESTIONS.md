# Interview question bank (answers grounded in this repository)

**Quant research / statistics**
1. Why can't you trust the best Sharpe ratio out of 64 backtests? → expected max SR of N null trials (False Strategy Theorem); DSR in `stats/overfitting.py`; test `test_dsr_penalises_selection_from_many_null_trials`.
2. What does PBO measure and how is CSCV computed? → `pbo_cscv`; fraction of IS/OOS splits where the IS-best config ranks below the OOS median.
3. Why a stationary (block) bootstrap instead of iid? → volatility clustering/autocorrelation.
4. Your DSR rejected a strategy with +900% OOS return on synthetic data. What was wrong? → pooled cross-family SR variance; fixed to within-family with a 1/T floor (ADR 0005).
5. How do you keep a holdout honest over months of research? → `ResearchSplit`, audited `open_holdout`, registry.

**Backtesting / data**
6. Forward vs back adjustment — which is point-in-time and why does it matter? (ADR 0003)
7. How do you prove a strategy has no look-ahead? → truncation invariance + strict-PIT engine equivalence.
8. How do you handle a stop when the stock gaps below it? → fill at open (`stop_gap`).
9. How is survivorship bias handled? → only by data choice (bhavcopy is point-in-time); delisting write-off rule A-D5.

**Economics / microstructure**
10. Why is DP charge the key number at ₹5k? → ₹15.93 per sell ≈ 0.64% of a ₹2,500 position; E4 cost drag table.
11. How did costs change strategy rankings? → E3: frictionless vs realistic vs stress.
12. Why hysteresis in momentum ranking? → Novy-Marx & Velikov cost mitigation.

**Software / reliability**
13. Walk through the order state machine and the UNKNOWN state.
14. A broker call times out. What does the system do, and why not retry? → reconcile, idempotency key.
15. Process dies after sending an order. How is it recovered? → write-ahead SQLite + restart reconcile (test).
16. Why a modular monolith and not microservices? (ADR 0001)
17. How do you make experiment history tamper-evident? → hash chain.
18. How did you optimise the backtester without risking correctness? → profile, targeted change, equality check, test suite.
18b. How do you know the paper path and the backtest agree? → shared `plan_orders`; equivalence test; it found a split-day risk-equity bug and a buy-shrinking vs broker-reject mismatch (ADR 0007).
18c. Why did crash recovery initially fill an order "before it was decided"? → post-close orders must be after-market orders eligible only in the next session.

**Risk / ML / portfolio**
19. Why can the risk engine veto the strategy, and why can't the strategy see it?
20. Why did the drawdown breaker need a cooldown? (RISK_MODEL.md)
21. When would you introduce ML, and what extra validation would it need? → purged CV/embargo, calibration, only after a Tier-1 edge.
22. Why not mean–variance optimisation with two positions?

What the system cannot reliably predict: future returns of any individual stock; regime changes in real
time; real slippage for its orders until paper-trading data exists.
