# ADR 0007 — The paper session must reproduce the backtest exactly

* Status: accepted
* Problem: backtest and paper execution are separate code paths; silent divergence makes backtests
  meaningless as predictors of paper (and later live) behaviour.
* Decision: both call one `plan_orders` (strategy → allocator → risk) with the same cost and slippage
  models; `tests/test_paper.py` requires equal equity curves (1e-6 INR) for every strategy.
* Defects this found (both fixed, with regression tests):
  1. Backtester computed the risk engine's start-of-day equity as post-split quantity × pre-split close
     on split ex-dates, inflating the drawdown peak and causing spurious drawdown halts.
  2. Backtester shrank unaffordable buys at the open; the paper broker (like a broker RMS for a cash
     account) rejects them. Backtester now rejects.
  3. (paper side) Crash recovery re-ran a day's open and filled orders placed after that day's close.
     Orders now carry the session after which they were placed (after-market orders).
* Consequence: results in EXPERIMENT_LOG were re-run after these fixes.
