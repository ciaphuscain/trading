"""Strategy tournament: identical capital, universe, period, costs, execution and risk
for every candidate; walk-forward OOS evaluation; promotion gate; markdown report."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestConfig, run_backtest
from ..data.market import MarketData
from ..stats import metrics as M
from ..strategies.library import REGISTRY
from .protocol import GateConfig, evidence_for, grid, period_sharpe, promotion_gate, walk_forward
from .registry import Registry

DEFAULT_GRIDS: dict[str, list[dict]] = {
    "M1_xs_momentum": grid(lookback=[63, 126, 252], skip=[0, 21], top_n=[1, 2], trend_filter=[0, 200]),
    "M2_ts_trend": grid(fast=[20, 50], slow=[100, 200], abs_lookback=[63, 126], atr_mult=[2.0, 3.0]),
    "R1_cond_mean_reversion": grid(entry_rsi=[5.0, 10.0, 20.0], trend_n=[100, 200], max_hold=[5, 10]),
    "B1_donchian_breakout": grid(entry_n=[20, 55], exit_n=[10, 20], volume_mult=[0.0, 1.5], atr_mult=[2.0, 3.0]),
}

HYPOTHESES = {
    "M1_xs_momentum": "Stocks with the highest past 3-12 month returns continue to outperform over the next month "
                      "(Jegadeesh & Titman 1993), net of NSE delivery costs at INR 5k scale.",
    "M2_ts_trend": "A stock's own positive medium-term trend predicts positive future returns "
                   "(Moskowitz, Ooi & Pedersen 2012, applied per stock).",
    "R1_cond_mean_reversion": "Short-term oversold pullbacks within long-term uptrends revert within days.",
    "B1_donchian_breakout": "Closes above the N-day high start persistent moves (Donchian/turtle breakout).",
}


def run_tournament(data: MarketData, *, cfg: BacktestConfig = BacktestConfig(), registry: Registry | None = None,
                   grids: dict[str, list[dict]] | None = None, train_days: int = 504, test_days: int = 126,
                   gate: GateConfig = GateConfig(), seed: int = 0) -> dict:
    grids = grids or DEFAULT_GRIDS
    registry = registry or Registry(None)
    # Baselines and benchmarks on the full research period.
    baselines = {}
    for name in ("C1_cash", "BH_buy_and_hold", *grids):
        res = run_backtest(REGISTRY[name](), data, cfg)
        baselines[name] = res
        registry.record_trial(family=f"{name}:default", strategy=name, params=res.strategy, data_version=res.data_version,
                              cost_version=res.cost_version, metrics=_clean(M.summarize(res)),
                              period_sr=period_sharpe(res.returns()), stage="baseline")
    bh_r = baselines["BH_buy_and_hold"].returns()
    families = {}
    for name, g in grids.items():
        wf = walk_forward(name, REGISTRY[name], g, data, train_days=train_days, test_days=test_days, cfg=cfg,
                          registry=registry, hypothesis=HYPOTHESES.get(name, ""))
        families[name] = wf
    # Charge every trial run so far (all families + baselines) - the honest denominator.
    n_trials, var_sr = registry.n_trials(), registry.var_trial_sr()
    rows, decisions = [], {}
    for name, wf in families.items():
        oos = wf.oos_returns
        trades = 0
        for _, f in wf.folds.iterrows():
            t = wf.runs[int(f.chosen)].trades
            if len(t):
                trades += int(((t.exit_date >= f.test_start) & (t.exit_date <= f.test_end)).sum())
        ev = evidence_for(oos, n_trials=n_trials, var_trial_sr=var_sr, benchmark_returns=bh_r, n_trades=trades,
                          pbo=wf.pbo.get("pbo", float("nan")), seed=seed)
        ev["is_sharpe_mean"] = float(wf.folds.is_sharpe_ann.mean()) if len(wf.folds) else float("nan")
        ev["oos_sharpe_mean"] = float(wf.folds.oos_sharpe_ann.mean()) if len(wf.folds) else float("nan")
        ev["grid"] = wf.grid_stats
        decision, reasons = promotion_gate(ev, gate)
        registry.record_decision(family=name, decision=decision, reasons=reasons, evidence=_clean_nested(ev))
        decisions[name] = (decision, reasons)
        start = oos.index[0] if len(oos) else data.dates[0]
        oos_inr = cfg.initial_cash * float((1 + oos).prod() - 1) if len(oos) else 0.0
        rows.append({"family": name, "decision": decision, "oos_start": str(start.date()),
                     "oos_years": round(ev["years"], 2), "oos_return": ev["total_return"],
                     "oos_profit_inr_on_5k": round(oos_inr, 2), "oos_sharpe": ev["sharpe_ann"],
                     "sharpe_ci_lo": ev["sharpe_ci95"][0], "sharpe_ci_hi": ev["sharpe_ci95"][1],
                     "is_sharpe_mean": ev["is_sharpe_mean"], "oos_sharpe_mean": ev["oos_sharpe_mean"],
                     "dsr": ev["dsr"], "pbo": ev["pbo"], "max_dd": ev["max_drawdown"], "oos_trades": trades,
                     "bench_return_same_period": float((1 + bh_r.reindex(oos.index).fillna(0)).prod() - 1)})
    base_rows = []
    for name, res in baselines.items():
        s = M.summarize(res)
        base_rows.append({"strategy": name, **{k: s[k] for k in ("profit_inr", "cagr", "sharpe", "max_drawdown",
                                                                 "n_trades", "fees_inr", "turnover_annual",
                                                                 "win_rate", "profit_factor", "avg_exposure")}})
    any_promoted = any(d == "PROMOTE_TO_PAPER" for d, _ in decisions.values())
    return {
        "data_version": data.version(),
        "data_source": data.source,
        "period": [str(data.dates[0].date()), str(data.dates[-1].date())],
        "n_symbols": len(data.symbols),
        "config": {"initial_cash": cfg.initial_cash, "broker": cfg.broker, "slippage": asdict(cfg.slippage),
                   "train_days": train_days, "test_days": test_days, "gate": asdict(gate)},
        "n_trials_charged": n_trials,
        "baselines": pd.DataFrame(base_rows),
        "walk_forward": pd.DataFrame(rows),
        "decisions": decisions,
        "families": families,
        "recommendation": ("Paper-trade the promoted candidate(s) only; live trading requires separate review."
                           if any_promoted else
                           "No candidate survived validation. Recommended action: hold cash or a passive index fund."),
    }


def _clean(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for k, v in d.items()}


def _clean_nested(d):
    if isinstance(d, dict):
        return {k: _clean_nested(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return [_clean_nested(v) for v in d]
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return str(d)
    return d


def _fmt(df: pd.DataFrame) -> str:
    def f(v):
        if isinstance(v, float):
            if math.isnan(v):
                return "n/a"
            return f"{v:.3f}" if abs(v) < 100 else f"{v:,.0f}"
        return str(v)
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(f(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_report(out: dict, path: str | Path, *, synthetic: bool) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    banner = ("> **SYNTHETIC DATA** - these numbers test the pipeline only and say NOTHING about real NSE "
              "profitability.\n" if synthetic else "")
    md = [
        "# OMEGA strategy tournament report", "", banner,
        f"* Data: `{out['data_source']}` version `{out['data_version']}`, {out['n_symbols']} symbols, "
        f"{out['period'][0]} to {out['period'][1]}",
        f"* Capital INR {out['config']['initial_cash']:,.0f}, broker profile `{out['config']['broker']}`, "
        f"walk-forward train {out['config']['train_days']} / test {out['config']['test_days']} sessions",
        f"* Trials charged to the Deflated Sharpe Ratio: **{out['n_trials_charged']}**",
        "* Returns are net of modelled charges and slippage, pre-tax.", "",
        "## Baselines (default parameters, full period, in-sample)", "", _fmt(out["baselines"]), "",
        "## Walk-forward out-of-sample results", "", _fmt(out["walk_forward"]), "",
        "## Decisions", "",
    ]
    for fam, (dec, reasons) in out["decisions"].items():
        md.append(f"* **{fam}: {dec}**" + ("" if not reasons else " - " + "; ".join(reasons)))
    md += ["", f"**Recommendation:** {out['recommendation']}", ""]
    (p / "report.md").write_text("\n".join(md), encoding="utf-8")
    out["baselines"].to_csv(p / "baselines.csv", index=False)
    out["walk_forward"].to_csv(p / "walk_forward.csv", index=False)
    summary = {k: v for k, v in out.items() if k not in ("baselines", "walk_forward", "families")}
    (p / "summary.json").write_text(json.dumps(_clean_nested(summary), indent=2, default=str), encoding="utf-8")
    return p / "report.md"
