# ADR 0003 — Forward (point-in-time) total-return adjustment

* Status: accepted
* Problem: back-adjusted prices change history whenever a new corporate action arrives, so a signal
  computed on day t can differ depending on when the data was downloaded (a subtle look-ahead).
* Decision: adjusted price = raw × cumulative factor of actions with ex-date ≤ t. Signals are ratio-based,
  so the level drift is irrelevant; truncation invariance is exact and tested.
* Cost: adjusted levels differ from vendor "adjusted close"; absolute-price rules must use RAW prices.
