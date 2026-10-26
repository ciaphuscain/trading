# OMEGA QUANT — Project Specification (Parts 1–5)

Status: v0.1, research + paper-trading foundation. Live trading: **not implemented**.
Everything below distinguishes what was *built and measured* from what is *assumed*.

---

## PART 1 — Project intelligence

### 1.1 Interpretation
OMEGA is a research instrument whose first job is to answer one question honestly:

> *Is there any systematic, long-only, low-turnover NSE cash-equity strategy that beats
> holding cash (or an index fund) for a ₹5,000 account, after every charge, with evidence
> that survives out-of-sample testing and selection-bias correction?*

"No" is an acceptable — and a priori the most likely — answer. The platform is built so
that "no" can be reached cheaply and defensibly, and so that "yes" requires passing a
pre-registered, mechanical gate (`omega/research/protocol.py::promotion_gate`).

### 1.2 Key assumptions (full register: [ASSUMPTIONS.md](ASSUMPTIONS.md))
* Long-only NSE cash equities (EQ series), delivery (CNC) trades, no leverage, no shorting, whole shares.
* Decisions on end-of-day data; execution at the next session's open.
* Charges per the dated schedule in `omega/config/charges_nse_equity_delivery.yaml` (UNVERIFIED rates).
* No paid data, broker API subscription, GPU or cloud.
* Operator runs Windows + PowerShell; the code is OS-independent Python ≥ 3.11.

### 1.3 Critical unknowns (must be resolved with primary sources before money is at risk)
| # | Unknown | Why it matters | How to resolve |
|---|---|---|---|
| U1 | Survivorship-free historical NSE data with corporate actions | Without delisted names and actions, every backtest is biased upward | Assemble from NSE bhavcopy archives + corporate-action files, or licence vendor data |
| U2 | Exact current charges at the operator's broker (DP fee especially) | DP fee ≈ 0.6% of a ₹2,500 sale; dominates economics | Read the broker's tariff page / a real contract note |
| U3 | Broker API cost and SEBI retail-algo rules | An API fee of a few hundred ₹/month exceeds plausible profit | Broker docs; SEBI circulars on retail algorithmic trading (2025) |
| U4 | Real slippage for small orders at the open | Model assumes 5 bps half-spread + impact | Paper-trade and compare model fills to actual open prints / contract notes |
| U5 | Tax treatment of frequent short-term gains | STCG at 20% (from 23 Jul 2024, UNVERIFIED) reduces net return | Tax professional |

### 1.4 Feasibility of trading ₹5,000 (computed with the bundled cost model)
`python -m omega costs --value 2500` (measured, see test `test_round_trip_on_small_ticket_is_material`):

| Item | Buy ₹2,500 | Sell ₹2,500 |
|---|---|---|
| STT (0.1%, rupee-rounded) | 3.00 | 3.00 |
| Exchange txn + SEBI | 0.07 | 0.07 |
| Stamp duty (0.015%) | 0.38 | – |
| DP charge (per scrip per sell day) | – | 13.50 |
| GST (18% on brokerage+exch+SEBI+DP) | 0.01 | 2.44 |
| **Total** | **3.46** | **19.01** |

Round trip ≈ **₹22.5 = 0.90%** of a ₹2,500 ticket, before slippage (~0.1% more).
Consequences:
* A strategy doing 12 round trips a year on two ₹2,500 slots pays ≈ ₹270/yr ≈ **5.4% of capital** in explicit charges.
* A risk-free liquid fund at ~6–7% earns ≈ ₹300–350/yr on ₹5,000 with no risk (rate UNVERIFIED).
* Even an excellent strategy (15% net) yields ≈ ₹750/yr. The *absolute* INR outcome is small; the
  principal value of the project at this capital is the research capability and evidence.
* Any monthly broker/API/data fee of ≥ ₹100 is economically fatal. OMEGA therefore needs only
  free EOD data and the operator can place the (few) orders manually.
* Experiment E3/E4 ([EXPERIMENT_LOG.md](EXPERIMENT_LOG.md)) measure this directly: high-turnover
  strategies lose roughly their entire cost bill on a market with no edge.

### 1.5 Main research challenges
1. Data: no free, survivorship-free, corporate-action-adjusted NSE history is bundled or reachable from the build environment.
2. Small sample: ₹5k supports 1–2 positions, so results are dominated by idiosyncratic noise; statistical power is low.
3. Selection bias: dozens of configurations are tried; the best in-sample result is expected to look good by chance.
4. Costs are a fixed-fee problem at this scale, not a basis-point problem.
5. Regime dependence of momentum/trend (momentum crashes) with few independent regimes in 10–15 years of Indian data.

### 1.6 Most important risks
| Risk | Type | Mitigation in code |
|---|---|---|
| Look-ahead bias | research | forward (point-in-time) adjustment; truncation-invariance tests for every indicator & strategy; strict-PIT reference engine equivalence test |
| Overfitting / selection bias | research | hash-chained registry counts every trial; DSR charged with all trials; PBO (CSCV); bootstrap CI; locked holdout with audited access |
| Unrealistic fills | research | next-open fills, slippage + impact, participation cap, gap-through-stop at open, missing-bar rejection |
| Accounting bugs | engineering | journal-based ledger, invariants checked every session, Hypothesis property tests |
| Costs mis-specified | financial | dated YAML schedule with status/source; sensitivity (frictionless/realistic/stress) |
| Duplicate / ambiguous orders | operational | deterministic idempotency keys, write-ahead persistence, UNKNOWN state, reconciliation, halt on break |
| Accidental live trading | safety | no live adapter exists; `OrderManager` refuses `is_live` brokers; `assert_live_allowed` always raises |

---

## PART 2 — Research foundation
See [RESEARCH_METHODS.md](RESEARCH_METHODS.md) for the bibliography (with what is verified
vs. recalled), and the per-paper reconstruction notes in [STRATEGIES.md](STRATEGIES.md).
Summary of the evidence position:

* **Verified in this repository (by measurement on synthetic data):** the research machinery
  (DSR, PBO, bootstrap, walk-forward, gate) rejects strategies on null markets and detects
  planted effects of sufficient size (E1/E2).
* **Historical findings (literature, not re-verified here):** cross-sectional momentum
  (Jegadeesh & Titman 1993) and time-series momentum (Moskowitz, Ooi & Pedersen 2012) earned
  premia in their samples; transaction costs erode many anomalies (Novy-Marx & Velikov 2016);
  most published factors fail stricter multiple-testing hurdles (Harvey, Liu & Zhu 2016).
* **Hypotheses (untested on real NSE data in this build):** that any of these survive NSE
  delivery costs at ₹5k scale.

### Open-source evaluation (decision: option C — own small event-driven simulator)
| Candidate | Fit for OMEGA | Decision |
|---|---|---|
| QuantConnect LEAN (Apache-2.0, C#) | Mature, but heavy (.NET), NSE data/cost model not native, overkill for EOD ₹5k | Not used; reference for order-state design |
| vectorbt (open-source edition, Apache-2.0 + Commons Clause) | Very fast vectorised research; whole-share, DP-per-scrip-day charges and dated schedules need custom work; licence restricts commercial use | Not used in v0; candidate for a fast *screening* layer later, validated against the reference engine |
| Backtrader (GPL-3.0) | Event-driven, but low maintenance activity in recent years | Not used |
| Qlib (MIT) | ML research platform, China-market oriented, heavy | Deferred to Phase 4 evaluation |
| Optuna / LightGBM / XGBoost / CVXPY / MLflow | Useful later | Not needed until ML/optimisation phases (no evidence yet that they help) |

Why custom (C): the dominant correctness risks at this scale are *accounting and cost* details
(whole shares, per-scrip DP fees, STT rounding, gap fills, corporate actions) that general
frameworks do not model for NSE; the engine is ~300 lines, fully tested, and fast enough
(measured in [BENCHMARKS.md](BENCHMARKS.md)). Licence and maintenance facts above are from the
author's knowledge and should be re-checked before adopting any of these packages.

---

## PART 3 — Architecture decision
Compared (details: [ARCHITECTURE.md](ARCHITECTURE.md), [adr/0001](adr/0001-modular-monolith.md)):

| | A. Modular monolith | B. Monolith + scheduled workers | C. Distributed services |
|---|---|---|---|
| Correctness | single process, deterministic | same + scheduler concerns | network failure modes added |
| Effort | lowest | low | high |
| Ops burden | none | Task Scheduler entry | containers, brokers, monitoring |
| Cost | ₹0 | ₹0 | ≥ hosting |
| Needed now? | **yes** | at paper-trading time (one daily job) | no measurable requirement |

**Decision: A now, B when the daily paper-trading job is added** (one Windows Task Scheduler
entry calling `python -m omega ...` after market close). C is rejected until a measured need exists.

---

## PART 4 — Quantitative strategy specification
Full mathematical definitions, pseudocode, entry/exit/risk rules: [STRATEGIES.md](STRATEGIES.md).

## PART 5 — Repository and implementation plan
Structure and dependency direction: [ARCHITECTURE.md](ARCHITECTURE.md).
Prioritised requirements (P0–P4/REJECT): [REQUIREMENTS.md](REQUIREMENTS.md).
Phase status: [../README.md#status](../README.md#status).
