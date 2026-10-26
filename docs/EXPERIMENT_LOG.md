# Experiment log

> **All experiments below use SYNTHETIC data** (`omega.data.sources.synthetic_market`): a one-factor,
> regime-switching market with optional planted momentum (an AR(1) drift per stock). They test whether
> the research machinery behaves correctly. **They say nothing about real NSE profitability, which
> remains unmeasured** because no real data was reachable from the build environment.

**Version note (v2):** all tables below were re-run after the fixes in ADR 0007 (split-day risk equity;
unaffordable buys rejected instead of shrunk). Headline conclusions did not change (E1 0/16, E2 the same
4/16 promoted); individual numbers moved slightly where noted, e.g. E1 seed 102 B1 OOS return +66% → +34%.

Reproduce: `python scripts/run_experiments.py --out results/experiments` (raw CSVs copied to
[`docs/experiments/`](experiments/)). Seeds 101–104, 30 symbols, 1,500 sessions (~6 years), ₹5,000,
`discount_zero_delivery` costs, default slippage, default risk limits, walk-forward 504/126, default grids
(68 configurations + 6 baselines = 74 trials charged per tournament).

## E1 — Null market (no planted structure): does the protocol promote noise?
Result: **0 of 16** family-seed combinations promoted.

| seed | family | naive OOS return | OOS Sharpe | DSR | PBO | decision |
|---|---|---|---|---|---|---|
| 102 | M1 momentum | **+96.5%** | 0.77 | 0.19 | 0.69 | REJECT |
| 102 | M2 trend | **+120.0%** | 0.89 | 0.14 | 0.02 | REJECT |
| 103 | B1 breakout | **+111.7%** | 0.92 | 0.27 | 0.25 | REJECT |
| 104 | all | −22% … −37% | < 0 | 0.00 | – | REJECT |
| all | R1 mean reversion | −32% … −61% | < −1.0 | 0.00 | > 0.8 | REJECT |

Lesson: with 1–2 positions, **noise alone produced >100% out-of-sample returns** over ~4 years in some seeds.
Walk-forward OOS is not sufficient protection by itself; the trial-count-aware DSR is what rejected them.

## E2 — Planted momentum (strength 3): does the protocol detect a real edge? (power)
Result: **4 of 16** promoted — M2 and B1 on seeds 101 and 102. M1 was never promoted; R1 (no reversal was
planted) never.

| seed | family | OOS return | OOS Sharpe (ann.) | DSR | PBO | OOS trades | decision / main failing rule |
|---|---|---|---|---|---|---|---|
| 101 | M2 | +1,122% | 2.70 | 0.993 | 0.33 | 55 | PROMOTE |
| 101 | B1 | +657% | 2.53 | 0.996 | 0.37 | 55 | PROMOTE |
| 101 | M1 | +963% | 2.53 | 0.56 | 0.00 | 17 | REJECT: DSR, trades < 30 |
| 102 | M2 / B1 | +1,902% / +1,171% | 2.75 / 2.48 | ≥ 0.994 | ≤ 0.28 | 45 / 42 | PROMOTE |
| 103 | B1 | +463% | 1.89 | 0.91 | 0.31 | 62 | REJECT: DSR < 0.95 |
| 104 | M2 | +376% | 1.73 | 0.85 | 0.03 | 74 | REJECT: DSR < 0.95 |

Lessons:
* The gate is **strict, with low power**: real (planted) edges with OOS Sharpe ≈ 1.7–1.9 over ~4 years were
  rejected. False negatives are the accepted price of avoiding E1-type false positives (ADR 0005).
* The **"≥ 30 OOS trades" rule penalises low-turnover strategies** (M1 holds positions for months, so few
  trades close in each test window), which conflicts with the project's preference for low turnover.
  **Open protocol question, to be decided before any real data is examined:** replace closed-trade count with
  number of independent rebalance decisions, or extend OOS length. Not changed now, because changing a gate
  after seeing its results is exactly the behaviour the protocol exists to prevent.
* The planted signal (strength 3) is unrealistically strong; it was chosen to verify detection, not realism.

## E2-pre — Bug found by E2 and fixed
The first E2 run (before the fix) rejected M1 with OOS +963% and DSR **0.006**. Cause: the DSR's
variance of trial Sharpe ratios was pooled across all strategy families and baselines, so it measured genuine
between-strategy differences (cash ≈ 0, R1 strongly negative) instead of selection noise, inflating the
expected-maximum-Sharpe hurdle. Fix (`research/tournament.py`): N = all trials (unchanged), variance =
within-family dispersion floored at the null sampling variance 1/T. E1/E2 above were re-run after the fix.

## E3 — How costs change strategy rankings (default parameters, mean of 4 seeds)
CAGR by cost regime ("frictionless" = zero charges and zero slippage; "stress" = hypothetical 0.3% brokerage + ₹20 DP):

| Market | Strategy | Frictionless | Realistic | Stress | Fees ₹ (realistic) | Trades |
|---|---|---|---|---|---|---|
| null | B1 breakout | +12.9% | +4.1% | +1.2% | 1,588 | 76 |
| null | M1 momentum | +2.3% | −1.3% | −4.0% | 813 | 39 |
| null | M2 trend | +2.7% | **−5.6%** | −1.0% | 2,021 | 98 |
| null | R1 mean reversion | +0.7% | **−14.9%** | −1.0% | 2,924 | 154 |
| null | Buy & hold | −3.0% | −3.0% | −2.9% | 4 | 0 |
| planted | M2 trend | +67.4% | +60.7% | +24.6% | 2,887 | 70 |
| planted | M1 momentum | +64.5% | +62.9% | +26.7% | 1,293 | 26 |
| planted | B1 breakout | +51.2% | +46.1% | −0.9% | 2,218 | 68 |
| planted | R1 mean reversion | +7.1% | **−14.8%** | −1.0% | 3,396 | 175 |

Ranking changes: on the null market M2 falls from 2nd (frictionless) to 4th (realistic), behind buy-and-hold;
on the planted market M1 overtakes M2 once costs apply, and B1 collapses from 3rd to 4th under stress costs.
Under stress costs the allocator's 2% round-trip cost cap blocks most entries (trade counts fall from ~70 to ~5–34),
i.e. the cost-aware filter, not the signal, determines behaviour. R1 (≈150–175 trades) loses ~15%/yr on both
markets with realistic costs: its gross edge is ≈ 0 and charges consume the account.

## E4 — Capital scale: cost drag of the same strategies (null market, mean of 2 seeds)
| Strategy | Capital | Trades | Explicit charges ₹ | of which DP ₹ | Charges as % of avg equity per year |
|---|---|---|---|---|---|
| M1 momentum | 5,000 | 39 | 812 | 520 | **2.5%** |
| M1 momentum | 50,000 | 40 | 3,029 | 533 | 0.9% |
| M1 momentum | 5,00,000 | 39 | 21,985 | 520 | 0.7% |
| B1 breakout | 5,000 | 76 | 1,616 | 1,026 | **4.8%** |
| B1 breakout | 50,000 | 79 | 6,268 | 1,067 | 1.7% |
| B1 breakout | 5,00,000 | 76 | 48,553 | 1,026 | 1.2% |
| R1 mean reversion | 5,000 | 174* | 3,271 | 2,342 | **18.0%** |
| R1 mean reversion | 50,000 | 364 | 19,917 | 4,907 | 8.7% |
| R1 mean reversion | 5,00,000 | 373 | 1,35,613 | 5,029 | 6.0% |

\* fewer trades at ₹5k because the drawdown breaker halted buying for cooldown periods.
DP charges are a fixed ₹15.93 per exit, so their weight falls tenfold with each tenfold increase in capital;
at ₹5k they are the majority of all explicit charges. Percentage costs (STT, stamp) do not shrink with scale.

## E5 — Performance
See [BENCHMARKS.md](BENCHMARKS.md).

## E6 — Paper session vs backtest (equivalence)
Day-by-day `PaperSession` runs (order manager + paper broker + reconciliation) on synthetic data
(10–12 symbols, 380–500 sessions, planted momentum) reproduce the backtester's equity curve with maximum
absolute difference **0.0 INR** for all four strategies, including stops, gap stops, a split and a dividend
(`tests/test_paper.py`). Before the fixes in ADR 0007 the maximum difference was ₹1,873 (B1) and ₹15 (R1).

## Decisions taken from these experiments
1. Keep the conservative gate unchanged; record the low-turnover/trade-count tension as an open item (above).
2. Prefer low-turnover candidates (M1, M2) for real-data research; treat R1 as rejected on cost grounds at ₹5k
   unless real data shows an unusually large gross edge.
3. DSR variance fix (E2-pre) adopted.
