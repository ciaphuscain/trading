# Strategy specifications (Tier 1)

Notation: $P_{i,t}$ forward-adjusted (total-return) close of stock $i$ after session $t$;
$\tilde P_{i,t}$ raw close; $H,L$ adjusted high/low; $\mathrm{SMA}_n$ simple moving average;
$\sigma^{(n)}_{i,t}$ annualised realised volatility of log returns over $n$ sessions;
$\mathrm{ATR}_n$ mean true range. All quantities at $t$ use only data $\le t$ (enforced by
`tests/test_lookahead.py`). Decisions at the close of $t$ execute at the open of $t+1$.

## Common rules (all strategies)
* **Universe eligibility at $t$** (`Strategy.eligible`): bar exists at $t$; 20-day median traded
  value ≥ ₹50 lakh (liquidity, hypothesis); raw price ≥ ₹10; raw price ≤ equity × (1 − 2%) / slots
  (a whole share must be affordable).
* **Sizing** (`portfolio/allocator.py`): slot weight $w = 1/\text{top\_n}$; shares
  $q=\lfloor w\,E\,(1-b) / (\tilde P (1+s))\rfloor$ reduced until cost-inclusive outlay fits, with
  cash buffer $b=2\%$, slippage estimate $s$. Orders below ₹500 or with estimated round-trip cost > 2%
  are skipped. Held positions are resized only if off target by > 25% of target (turnover band).
* **Risk** (`risk/engine.py`): every order is checked independently; see [RISK_MODEL.md](RISK_MODEL.md).
* Stops are distances below the decision close, converted to raw price at entry; a gap below the
  stop fills at the open (worse than the stop).

## C1 — Cash (null strategy)
Never trades. Every candidate must beat it after costs. Also the system's default recommendation.

## BH — Buy and hold (benchmark)
At the first eligible session after a 21-session warm-up, buy the `n=2` most liquid affordable
stocks equally; never trade again. A Nifty 50 TRI series can additionally be supplied as
`benchmark.csv` (non-tradable).

## M1 — Cross-sectional momentum (Jegadeesh & Titman 1993; long-only top bucket)
Score: $m_{i,t} = P_{i,t-\text{skip}} / P_{i,t-\text{lookback}} - 1$, optionally
$m_{i,t}/\sigma^{(63)}_{i,t}$ (risk-adjusted).

```
every `rebalance` sessions after warm-up:
    if trend_filter>0 and EW-universe index < SMA(trend_filter): exit all (regime gate)
    ranked  = top (top_n + hysteresis) by score among eligible ∪ held
    keep    = held names inside `ranked`                      # hysteresis band
    new     = best eligible non-held names to fill top_n slots
    target  = keep (unchanged size) + new (weight 1/top_n); others exited
```
Defaults: lookback 126, skip 21, top_n 2, rebalance 21, hysteresis 2. Grid in
`research/tournament.py::DEFAULT_GRIDS` (lookback {63,126,252} × skip {0,21} × top_n {1,2} ×
trend filter {off, 200}).
Deviations from the paper: J&T form decile portfolios of many stocks with overlapping holding
periods and include shorts; OMEGA holds 1–2 stocks long-only because of capital. This is a large
deviation: diversification is absent, so idiosyncratic risk dominates.

## M2 — Time-series trend (inspired by Moskowitz, Ooi & Pedersen 2012)
In-trend: $P_t > \mathrm{SMA}_{fast} > \mathrm{SMA}_{slow}$ and $P_t/P_{t-k}-1>0$ (absolute momentum).
Rank entrants by $(P_t/P_{t-k}-1)/\sigma^{(63)}_t$. Exit when $P_t < \mathrm{SMA}_{fast}$ or stop at
$\text{atr\_mult}\cdot\mathrm{ATR}_{20}/P_t$ below entry decision price.
Deviation: MOP study futures across asset classes with volatility-scaled positions; OMEGA applies the
idea to single stocks with equal slots.

## R1 — Conditional mean reversion
Entry: $\mathrm{RSI}_2 < 10$ **and** $P_t > \mathrm{SMA}_{200}$ (only buy pullbacks inside uptrends).
Rank by lowest RSI. Exit: $\mathrm{RSI}_2 > 70$, or $P_t > \mathrm{SMA}_5$, or held ≥ 10 sessions,
or ATR stop (3×ATR10). This rule family is practitioner lore (popularised in trading books), not a
peer-reviewed result; it is included as a short-horizon contrast whose turnover is expected to be
uneconomic at ₹5k. E3/E4 confirm that cost drag dominates it.

## B1 — Donchian breakout
Entry: $P_t > \max(H_{t-n..t-1})$ (n=55), optional volume confirmation
$V_t > k\cdot\overline{V}_{20}$; rank by $(P_t-\max H)/\mathrm{ATR}_{20}$.
Exit: $P_t < \min(L_{t-m..t-1})$ (m=20) or 2×ATR stop.

## Parameter search policy
Grids are small, fixed in code before running, and every configuration run is recorded in the
registry and charged to the Deflated Sharpe Ratio. Adding grid points after seeing results is
allowed only as a new, recorded trial set — never by deleting old ones (the hash chain detects it).
