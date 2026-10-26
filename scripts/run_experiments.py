"""Reproducible experiments on SYNTHETIC data (no real NSE data was available in the
build environment). They test the research machinery, not real-market profitability.

    python scripts/run_experiments.py --out results/experiments

E1  null market: does the protocol ever promote a strategy when there is nothing to find?
E2  planted momentum: does it detect a real (synthetic) edge? (power)
E3  cost sensitivity: how do charges change strategy rankings?
E4  capital scale: cost drag at INR 5k vs 50k vs 5L
E5  performance: backtest / tournament throughput
"""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd

from omega.backtest.engine import BacktestConfig, run_backtest
from omega.data.sources import synthetic_market
from omega.research.tournament import run_tournament
from omega.stats.metrics import summarize
from omega.strategies.library import REGISTRY

ZERO = str(Path(__file__).resolve().parent.parent / "omega" / "config" / "charges_zero_reference.yaml")
STRATS = ["BH_buy_and_hold", "M1_xs_momentum", "M2_ts_trend", "R1_cond_mean_reversion", "B1_donchian_breakout"]


def e1_e2(seeds, strength, label):
    rows = []
    for seed in seeds:
        md = synthetic_market(n_symbols=30, n_days=1500, seed=seed, momentum_strength=strength)
        t = time.perf_counter()
        out = run_tournament(md, seed=seed)
        dt = time.perf_counter() - t
        for _, r in out["walk_forward"].iterrows():
            rows.append({"experiment": label, "seed": seed, "family": r.family, "decision": r.decision,
                         "oos_return": r.oos_return, "oos_sharpe": r.oos_sharpe, "is_sharpe_mean": r.is_sharpe_mean,
                         "oos_sharpe_mean": r.oos_sharpe_mean, "dsr": r.dsr, "pbo": r.pbo, "oos_trades": r.oos_trades,
                         "bench_return": r.bench_return_same_period, "n_trials": out["n_trials_charged"],
                         "runtime_s": round(dt, 1)})
        print(label, seed, f"{dt:.1f}s", out["walk_forward"][["family", "decision", "oos_return", "dsr", "pbo"]]
              .to_string(index=False), flush=True)
    return pd.DataFrame(rows)


def e3(seeds):
    rows = []
    for strength in (0.0, 3.0):
        for seed in seeds:
            md = synthetic_market(n_symbols=30, n_days=1500, seed=seed, momentum_strength=strength)
            for regime, cfg in {"frictionless": BacktestConfig(cost_schedule=ZERO,
                                                               slippage=_noslip()),
                                "realistic": BacktestConfig(),
                                "stress": BacktestConfig(broker="stress_high_cost")}.items():
                for name in STRATS:
                    res = run_backtest(REGISTRY[name](), md, cfg)
                    s = summarize(res)
                    rows.append({"strength": strength, "seed": seed, "costs": regime, "strategy": name,
                                 "cagr": s["cagr"], "profit_inr": s["profit_inr"], "fees_inr": s["fees_inr"],
                                 "n_trades": s["n_trades"], "sharpe": s["sharpe"]})
    return pd.DataFrame(rows)


def _noslip():
    from omega.costs.model import SlippageModel

    return SlippageModel(half_spread_bps=0.0, impact_coef_bps=0.0)


def e4(seeds):
    rows = []
    for seed in seeds:
        md = synthetic_market(n_symbols=30, n_days=1500, seed=seed, momentum_strength=0.0)
        for cash in (5_000.0, 50_000.0, 500_000.0):
            for name in ("M1_xs_momentum", "R1_cond_mean_reversion", "B1_donchian_breakout"):
                res = run_backtest(REGISTRY[name](), md, BacktestConfig(initial_cash=cash))
                s = summarize(res)
                avg_eq = float(res.equity.mean())
                rows.append({"seed": seed, "capital": cash, "strategy": name, "n_trades": s["n_trades"],
                             "explicit_costs_inr": res.costs.get("explicit_total", 0.0),
                             "dp_inr": res.costs.get("dp", 0.0), "slippage_inr": res.costs.get("slippage", 0.0),
                             "cost_drag_pct_per_year": 100 * res.costs.get("explicit_total", 0.0) / avg_eq / s["years"],
                             "cagr": s["cagr"]})
    return pd.DataFrame(rows)


def e5():
    rows = []
    for n_sym, n_days in ((30, 1500), (100, 2500), (200, 2500)):
        md = synthetic_market(n_symbols=n_sym, n_days=n_days, seed=1, momentum_strength=1.0)
        for name in ("M1_xs_momentum", "B1_donchian_breakout"):
            ts = []
            for _ in range(3):
                t = time.perf_counter()
                run_backtest(REGISTRY[name](), md)
                ts.append(time.perf_counter() - t)
            tracemalloc.start()
            run_backtest(REGISTRY[name](), md)
            peak = tracemalloc.get_traced_memory()[1] / 2**20
            tracemalloc.stop()
            rows.append({"symbols": n_sym, "sessions": n_days, "strategy": name, "median_s": float(np.median(ts)),
                         "symbol_sessions_per_s": n_sym * n_days / float(np.median(ts)), "peak_mem_mb": round(peak, 1)})
    md = synthetic_market(n_symbols=10, n_days=400, seed=1)
    t = time.perf_counter()
    run_backtest(REGISTRY["M1_xs_momentum"](lookback=60, skip=5), md, BacktestConfig(strict_pit=True))
    strict = time.perf_counter() - t
    t = time.perf_counter()
    run_backtest(REGISTRY["M1_xs_momentum"](lookback=60, skip=5), md)
    fast = time.perf_counter() - t
    rows.append({"symbols": 10, "sessions": 400, "strategy": "M1 strict_pit vs fast", "median_s": strict,
                 "symbol_sessions_per_s": 4000 / strict, "peak_mem_mb": None, "fast_s": fast})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/experiments")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--only", default="1,2,3,4,5")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = list(range(101, 101 + a.seeds))
    only = set(a.only.split(","))
    if "5" in only:
        df = e5(); df.to_csv(out / "e5_performance.csv", index=False); print(df.to_string(), flush=True)
    if "4" in only:
        df = e4(seeds[:2]); df.to_csv(out / "e4_capital_scale.csv", index=False)
        print(df.groupby(["strategy", "capital"])[["n_trades", "cost_drag_pct_per_year", "cagr"]].mean(), flush=True)
    if "3" in only:
        df = e3(seeds); df.to_csv(out / "e3_cost_sensitivity.csv", index=False)
        print(df.groupby(["strength", "costs", "strategy"])[["cagr", "fees_inr", "n_trades"]].mean(), flush=True)
    if "1" in only:
        df = e1_e2(seeds, 0.0, "E1_null"); df.to_csv(out / "e1_null.csv", index=False)
    if "2" in only:
        df = e1_e2(seeds, 3.0, "E2_planted"); df.to_csv(out / "e2_planted.csv", index=False)
    (out / "DONE").write_text(json.dumps({"seeds": seeds}))


if __name__ == "__main__":
    main()
