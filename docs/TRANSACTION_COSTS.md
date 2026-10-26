# Transaction costs

Explicit charges: `omega/costs/model.py::CostModel`, schedule `omega/config/charges_nse_equity_delivery.yaml`.
Each schedule row has `effective_from`, `status` and `source`; the version in force on the trade date is
used (`test_schedule_is_dated`). Rates are UNVERIFIED (see ASSUMPTIONS A-C1..C7).

Per executed delivery order of value V on date d:
* brokerage = profile-specific (`discount_zero_delivery`: 0; `flat_20_or_0p1pct`: min(0.1%·V, ₹20))
* STT = round_half_up(0.1% · V) (buy and sell)
* exchange = rate(d) · V; SEBI = 1e-6 · V
* stamp = 0.015% · V (buy only)
* DP = ₹13.50 on the **first sell of a scrip per day** (sell only)
* GST = 18% · (brokerage + exchange + SEBI + DP)
Each component is rounded half-up to the paisa.

Implicit costs (`SlippageModel`): half-spread 5 bps + impact 10 bps·√participation; participation cap 2%.

Hand-checked example (₹2,500, 2025): buy ₹3.46, sell ₹19.01, round trip 0.90% (tests in `tests/test_costs.py`).

Profiles for sensitivity: `discount_zero_delivery` (default), `flat_20_or_0p1pct`, `stress_high_cost`
(hypothetical), plus `charges_zero_reference.yaml` (frictionless, research only).
Measured effect on strategy rankings: [EXPERIMENT_LOG.md](EXPERIMENT_LOG.md) E3/E4.

To update a tariff: add a new row with a later `effective_from` (never edit history), cite the source,
set `status`, and add a test for the new values.
