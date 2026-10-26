# ADR 0005 — Mechanical, conservative promotion gate; cash as default

* Status: accepted
* Problem: humans (and LLMs) rationalise good-looking backtests.
* Decision: promotion to paper trading requires all of: ≥2y OOS, DSR ≥ 0.95 with all registry trials
  charged, bootstrap Sharpe CI lower bound > 0, PBO ≤ 0.5, ≥30 OOS trades, MDD ≤ 35%, beats cash and
  buy-and-hold. Otherwise the recommendation is cash/passive.
* Consequence: false negatives are accepted as the price of few false positives; E1/E2 measure both.
* Revision during development: DSR variance was first pooled across all strategy families; E2 showed it
  rejected even a very strong planted edge (DSR 0.006). Changed to within-family variance floored at the
  null sampling variance 1/T (ADR stays: N = all trials).
