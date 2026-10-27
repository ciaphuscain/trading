# Assumption register

Status values: **VERIFIED** (checked against a primary source *in this repository's history*),
**UNVERIFIED** (author knowledge; must be checked), **DESIGN** (a deliberate modelling choice),
**HYPOTHESIS** (a parameter whose value is a guess to be tested).
No external source could be fetched from the build environment (network policy blocked NSE and
data hosts), so no regulatory or tariff fact is marked VERIFIED.

| ID | Assumption | Status | Where used | Verify by |
|---|---|---|---|---|
| A-M1 | Capital ₹5,000, cash-only, long-only, no derivatives, no shorting, whole shares | DESIGN | everywhere | – |
| A-M2 | Decisions on EOD data, execution at next session open | DESIGN | backtest engine | – |
| A-C1 | STT 0.1% on delivery buy and sell, rounded to nearest rupee | UNVERIFIED | costs YAML | Finance Act / contract note |
| A-C2 | NSE transaction charge 0.00297% from 2024-10-01 (0.00322% before) | UNVERIFIED | costs YAML | NSE circular |
| A-C3 | SEBI turnover fee ₹10/crore | UNVERIFIED | costs YAML | SEBI |
| A-C4 | Stamp duty 0.015% on delivery buys from 2020-07-01; state-dependent before | UNVERIFIED | costs YAML | Indian Stamp Act amendment |
| A-C5 | GST 18% on brokerage + exchange + SEBI + DP charges | UNVERIFIED | costs YAML | contract note |
| A-C6 | DP charge ≈ ₹13.50 + GST per scrip per sell day | UNVERIFIED | costs YAML | broker tariff |
| A-C7 | Zero delivery brokerage at a discount broker | UNVERIFIED | broker profile | broker tariff |
| A-C8 | Pre-2020 charges approximated by one row | DESIGN (approximate) | costs YAML | – |
| A-S1 | Slippage = 5 bps half-spread + 10 bps × √participation | HYPOTHESIS | SlippageModel | paper-trading fill comparison |
| A-S2 | Fills limited to 2% of the fill day's volume | HYPOTHESIS | engine | – |
| A-D1 | Forward total-return adjustment is used for signals | DESIGN | data/market.py | – |
| A-D2 | Dividends credited on the ex-date (actual payment is later); TDS ignored | DESIGN (simplification) | engine | – |
| A-D3 | Bhavcopy column names (legacy and UDiFF) as in `sources.py` | UNVERIFIED | sources.py | download a current file |
| A-D7 | NSE archive URL patterns and the UDiFF switch date (2024-07-08) in `nse_archive.py` | UNVERIFIED | downloader | try one day manually |
| A-D8 | NSE corporate-action export columns (SYMBOL, SERIES, PURPOSE, EX-DATE) and PURPOSE phrasings | UNVERIFIED | corporate_actions.py | parse a real export; review `actions_unparsed.csv` |
| A-X2 | A session that trades at a single price (high = low) above/below the previous close is circuit-locked: buys (upper) / sells and stops (lower) cannot fill | HYPOTHESIS (daily-bar heuristic; true locks need order-book data) | backtest + paper | compare with real lock days |
| A-X1 | Orders placed after the close behave as after-market orders filled at the next open; unaffordable cash-account buys are rejected by the broker, not reduced | UNVERIFIED (typical RMS behaviour) | backtest + paper broker | broker documentation |
| A-D4 | Missing bar = not tradable that session | DESIGN | engine | – |
| A-D5 | No bar for 10 sessions = delisted; written off at 50% of last close | HYPOTHESIS | engine | delisting outcomes study |
| A-D6 | Fractional split entitlements paid as cash at the ex-date open | DESIGN (approximation) | ledger | – |
| A-R1 | All risk limits in `RiskLimits` | HYPOTHESIS | risk engine | RISK_MODEL.md |
| A-T1 | Listed-equity STCG 20% from 2024-07-23 (15% before) | UNVERIFIED | metrics.estimate_stcg_tax | tax professional |
| A-T2 | Reported returns are pre-tax | DESIGN | reports | – |
| A-L1 | SEBI's 2025 framework for retail algorithmic trading via broker APIs applies to any automated order placement | UNVERIFIED | live_guard.py | SEBI circular + broker |
| A-L2 | Broker API access may carry a monthly fee comparable to or larger than expected profit | UNVERIFIED | economics | broker pricing page |
| A-O1 | Liquid-fund / savings alternative yields ~6–7% p.a. | UNVERIFIED | feasibility | current rates |
| A-E1 | Synthetic data generator parameters (vol, regimes, momentum AR(1)) | DESIGN | experiments | – (synthetic by definition) |
