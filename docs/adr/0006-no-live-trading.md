# ADR 0006 — No live trading code in v0.x

* Status: accepted
* Decision: ship only PaperBroker. OrderManager refuses brokers with is_live=True; live_guard always raises.
* Rationale: no strategy has passed validation; broker/regulatory facts are unverified (ASSUMPTIONS A-L1/L2);
  at ₹5k the expected INR benefit of automation is smaller than its operational risk.
* Revisit only via the checklist in omega/execution/live_guard.py and an explicit human decision.
