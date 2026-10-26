# Backtest assumptions (reference engine `omega/backtest/engine.py`)

| Topic | Assumption | Test |
|---|---|---|
| Information timing | Strategy sees data ≤ close of t; orders fill at open of t+1 | `test_hand_calculated_trade_next_open_execution`, look-ahead tests |
| Point-in-time | Fast path equals re-preparing on truncated data each session | `test_fast_engine_matches_strict_point_in_time_reference` |
| Prices | Fills, sizing, marking on RAW prices; signals on forward-adjusted prices | basis guard in `PriceFrame.require` |
| Order of events per session | corporate actions → sells → buys at open → stops → delisting → mark → decide | engine docstring |
| Slippage | adverse: 5 bps half spread + 10 bps·√(qty/day volume) | `test_slippage_is_adverse` |
| Liquidity | fill ≤ 2% of the session's volume; remainder expires (partial fill event) | events log |
| Whole shares / cash | quantities are ints; a buy whose cost-inclusive outlay exceeds cash at the open is rejected (as a broker RMS would), not shrunk | `test_unaffordable_buy_at_open_is_rejected_not_shrunk`, ledger property tests |
| Stops | triggered when session low ≤ stop; fill at stop, or at the open if it gapped through | `test_gap_through_stop_fills_at_open_not_stop` |
| Missing bar | orders for a symbol with no bar are rejected (not carried) | `test_order_for_missing_bar_is_rejected_not_filled` |
| Splits/bonuses | share count × multiplier; fractional entitlement → cash; pending orders adjusted | `test_split_keeps_equity_continuous_and_dividend_credited` |
| Dividends | cash credited on ex-date (simplification: real payment later; TDS ignored) | same |
| Delisting | no bar for 10 sessions ⇒ write-off at 50% of last close (HYPOTHESIS A-D5) | `test_delisted_position_is_written_off` |
| Marking | last available raw close (stale marks for suspended names) | – |
| Circuit limits | not modelled: a stock locked at upper/lower circuit is assumed tradable at the open | known gap |
| Intraday path | unknown; only OHLC; stop + exit on the same bar assumes stop first | known gap |
| Taxes | excluded from returns; STCG estimate reported separately | `test_stcg_estimate_by_financial_year` |
| Determinism | no randomness in the engine; identical inputs ⇒ identical outputs | `test_deterministic_replay` |
| Risk | every order passes the independent `RiskEngine` before becoming pending | `test_risk_engine_can_veto_everything` |

Invariants asserted every session (`Ledger.check_invariants`): cash equals the journal sum, cash ≥ 0,
per-symbol quantities equal the journal sum, quantities are positive ints, no duplicate order ids.
