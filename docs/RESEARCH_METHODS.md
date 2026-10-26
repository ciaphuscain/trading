# Research methods and bibliography

## Bibliography
"Status" says what was done with each source in this build. No paper could be downloaded from the
build environment; details below are from the author's knowledge of the literature and must be
checked against the originals before being cited elsewhere. Nothing here is claimed as a current
profitability result.

| Reference | Market / sample (as recalled) | Key method | Costs in paper | Use in OMEGA | Status |
|---|---|---|---|---|---|
| Jegadeesh & Titman (1993), *J. Finance* 48(1) "Returns to Buying Winners and Selling Losers" | US NYSE/AMEX, 1965–1989 | J-month formation / K-month holding, decile long-short, overlapping portfolios, often a 1-week skip | Discussed, not central | M1 (long-only top bucket, 1–2 stocks) | recalled, not re-read |
| Moskowitz, Ooi & Pedersen (2012), *JFE* 104(2) "Time Series Momentum" | 58 futures/forwards, 1985–2009 | sign of past 12-month excess return, volatility-scaled positions | Low (futures) | M2 inspiration | recalled |
| Asness, Moskowitz & Pedersen (2013), *J. Finance* 68(3) "Value and Momentum Everywhere" | 8 markets/asset classes | value + momentum combinations, negative correlation | – | Tier-2 idea (needs fundamentals data) | recalled |
| Novy-Marx & Velikov (2016), *RFS* 29(1) "A Taxonomy of Anomalies and Their Trading Costs" | US anomalies | effective spread costs; cost-mitigation (buy/hold spreads, i.e. hysteresis) | Central | hysteresis band in M1, rebalance band in allocator, cost sensitivity E3 | recalled |
| Bailey & López de Prado (2012) "The Sharpe Ratio Efficient Frontier", *J. Risk* | – | Probabilistic Sharpe Ratio | – | `probabilistic_sharpe_ratio` | formula implemented, tested |
| Bailey & López de Prado (2014) "The Deflated Sharpe Ratio", *J. Portfolio Mgmt* | – | expected max SR under N trials; DSR | – | `deflated_sharpe_ratio` with registry trial counts | implemented, tested |
| Bailey, Borwein, López de Prado & Zhu (2017) "The Probability of Backtest Overfitting", *J. Comp. Finance* | – | CSCV, PBO | – | `pbo_cscv` | implemented, tested |
| Harvey, Liu & Zhu (2016), *RFS* 29(1) "…and the Cross-Section of Expected Returns" | US factors | multiple-testing adjustments; t > 3 hurdle | – | Holm/BH, DSR, conservative gate | recalled |
| López de Prado (2018) *Advances in Financial Machine Learning* | – | purged k-fold, embargo, meta-labeling | – | Phase 4 (purging needed only with overlapping labels) | recalled |
| Engle (1982), *Econometrica*; Bollerslev (1986) GARCH | – | volatility clustering | – | stationary block bootstrap; vol-scaled ranking; Tier-2 vol forecasting | recalled |
| Politis & Romano (1994), *JASA* "The Stationary Bootstrap" | – | geometric-block bootstrap | – | `stationary_bootstrap_ci` | implemented |
| Avellaneda & Stoikov (2008), *Quant. Finance* "High-frequency trading in a limit order book" | – | market-making | – | REJECT for ₹5k EOD (reference only) | recalled |

## Protocol (implemented)
1. **Locked holdout.** `ResearchSplit(holdout_start).research(data)` hides everything on/after the date.
   `open_holdout` logs every access in the registry and refuses a second access per strategy family
   unless explicitly overridden (which is itself recorded).
2. **One continuous causal run per configuration**, then **walk-forward selection**: rolling 504-session
   train / 126-session test windows; the best training-window configuration is used on the next test
   window; test windows are stitched into the OOS record. In-sample vs OOS Sharpe per fold measure
   degradation.
3. **Trial accounting.** Every configuration (including baselines) is a registry record; the DSR is charged
   with the total trial count and the cross-trial variance of per-period Sharpe ratios.
4. **PBO** by CSCV over the configuration grid (10 blocks, 252 combinations).
5. **Uncertainty.** Stationary-bootstrap 95% CI of the OOS Sharpe ratio (mean block 20 sessions).
6. **Benchmarks.** Cash (C1) and buy-and-hold (BH) on identical data, costs and execution.
7. **Promotion gate** (`promotion_gate`), all must pass: OOS ≥ 2 years; DSR ≥ 0.95; bootstrap CI lower
   bound > 0; PBO ≤ 0.5; ≥ 30 OOS trades; max drawdown ≤ 35%; beats cash; beats buy-and-hold. Failure ⇒
   **REJECT ⇒ hold cash/passive**. The gate is deliberately strict; with 1–2 positions and ~10 years of
   data, passing it is expected to be rare.
8. **Purged CV / embargo**: not needed for the rule-based Tier-1 strategies (no fitted labels). Required
   before any ML model (Phase 4).
9. **Regime analysis**: the M1 trend gate is the only regime component; formal regime detection
   (thresholds vs. HMM) is deferred until a Tier-1 candidate shows regime-dependent value.

## Known limitations of the protocol
* Configuration switching costs at walk-forward boundaries are not charged.
* PBO with highly correlated configurations is optimistic about the effective number of trials.
* The DSR assumes trials are independent draws; correlated grids overstate the penalty somewhat.
* With 1–2 positions the strategy return series is dominated by single-stock noise; bootstrap CIs are wide.
