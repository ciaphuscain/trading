# Operations runbook (research + paper)

## Research cycle
1. Put data in a directory (`bars.csv`, `actions.csv`, optional `benchmark.csv`), or convert bhavcopies with
   `omega.data.sources.read_bhavcopy`.
2. `python -m omega validate --data DIR --out runs/issues.csv` — fix every ERROR. Record the printed `data_version`.
3. Choose and **write down** the holdout start date *before* looking at results (e.g. the last 2 years).
4. `python -m omega tournament --data DIR --holdout-start YYYY-MM-DD --registry runs/registry.jsonl --out results/t1`
5. Read `results/t1/report.md`. A REJECT is a valid result; record it in docs/EXPERIMENT_LOG.md.
6. Only a family that passes the gate may be evaluated once on the holdout via
   `ResearchSplit.open_holdout` (logged). A second look is recorded and makes the holdout result biased.
7. `python -m omega registry --registry runs/registry.jsonl` must print `"verified": true`.

## Metrics that matter (and the question each answers)
| Metric | Question |
|---|---|
| Net profit INR vs cash and buy-and-hold | Is trading worth it at all? |
| Fees INR, DP INR, cost drag %/yr | Are charges eating the edge? |
| OOS vs IS Sharpe per fold | Is the edge degrading out of sample? |
| DSR, PBO, bootstrap CI | Could this be selection luck? |
| Max drawdown, CVaR | Can the operator live with the losses? |
| Risk rejections by reason | Is the strategy fighting the risk limits? |
| Events: partial fills, cash reductions, rejections, write-offs | Is execution realistic? |
| Paper: UNKNOWN orders, reconciliation breaks | Is the order path healthy? |

## Daily paper job
`python -m omega paper --data data_store\nse --strategy M1_xs_momentum --state runs\paper_m1 --backup runs\backups`
(or register `scripts/paper_daily.ps1` with Task Scheduler; it validates data first). It processes every
session not yet processed, so missed days are caught up; running twice is harmless. Exit code 3 = halted
(reconciliation break), 2 = data validation failed. Review `runs\paper_daily.log` and the printed alerts.
Inspect history in Python: `PaperSession(state_dir, strategy).history()`.

## Paper-trading incident handling
* Reconciliation break → `OrderManager.halted` is set; compare `local_positions()` with broker positions,
  correct the cause, record an incident note, then create a new manager session.
* Manual kill switch: create a file named `KILL_SWITCH` at the configured path (blocks all buys; sells allowed),
  or call `RiskEngine.set_kill_switch(True, who, why)` (audited).
* UNKNOWN order: never resubmit; run `reconcile()`; after two not-found results it becomes REJECTED and a fresh
  decision (new attempt number) may be made.
