# Risk model

`omega/risk/engine.py` is deterministic and independent of strategy code. Every order intent (backtest)
and every order request (paper `OrderManager`, via `risk_check`) passes it. It can reject everything.

| Control | Default (HYPOTHESIS) | Rationale | Applies to |
|---|---|---|---|
| Duplicate order id | reject | idempotency | all |
| Invalid qty/side/price, price-sanity (±10% vs last close) | reject | fat-finger / bad data | all |
| Stale data | last bar > 4 calendar days old ⇒ reject | weekends + one holiday | all |
| Orders per day | 6 | runaway-loop guard | all |
| Unowned sale | reject | cash account | sells |
| Kill switch (manual, file `KILL_SWITCH` or `set_kill_switch`, audited) | blocks buys | operator override | buys |
| Max drawdown from peak | 25% ⇒ block buys for 21 sessions, then re-arm from current equity | prevents compounding a broken strategy; cooldown avoids permanent lock-out that would make research meaningless | buys |
| Daily loss | 5% ⇒ block buys rest of day | – | buys |
| Max position | 60% of equity | with ₹5k, 2 slots ≈ 49% each; 1-slot configs are capped | buys |
| Gross exposure | 100% | no leverage | buys |
| New positions per day | 2 | – | buys |
| Liquidity | qty ≤ 2% of 20-day average volume | market impact | buys |
| Loss per trade | stop distance × position value ≤ 5% equity | sizing discipline | buys with stops |
| Min order value | 0 (allocator already enforces ₹500) | fixed charges | buys |

Risk-reducing sells are never blocked by drawdown/kill-switch rules.

**Stops do not cap losses.** A gap through the stop fills at the open (`stop_gap` event); a suspended
or circuit-locked stock cannot be sold at all. Worst-case loss per position is the full position value.

Design choice reversed during development: the drawdown rule originally halted buying **forever**. On
synthetic data this froze most strategies after their first 25% drawdown, so walk-forward OOS periods
contained zero trades and evaluation measured the breaker, not the strategy. It was changed to a
cooldown + re-arm rule (`drawdown_cooldown`, 0 restores halt-forever for live-like paper runs).
