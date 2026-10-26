# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `validate` reports `unexplained_jump` | split/bonus missing from `actions.csv` | add the action (split value = share multiplier) |
| `duplicate (date, symbol) bars` on load | overlapping downloads | `validate_long_bars`, keep one source per day |
| `no charge schedule in force on …` | backtest before the first schedule row | add an earlier (approximate, labelled) row |
| Strategy makes no trades | eligibility (liquidity ≥ ₹50 lakh/day, price ≤ equity/slots), allocator min order ₹500 or 2% cost cap, or risk rejections | inspect `result.events` and `result.risk_log` |
| Many `risk_rejected: drawdown_halt` | 25% drawdown breaker in cooldown | expected; review the strategy |
| Tournament says REJECT everywhere | probably correct | see EXPERIMENT_LOG; hold cash |
| `registry` verified = false | registry file edited or truncated | restore from backup; never edit by hand |
| PowerShell refuses to activate venv | execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `OrderManager refuses live brokers` | by design | live trading is not implemented (ADR 0006) |
