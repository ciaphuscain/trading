"""OMEGA command line.  Run ``python -m omega --help``.

Every command is research/paper only. There is no command that can place a live order.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def _load(args):
    from .data.sources import load_directory, synthetic_market

    if args.synthetic:
        return synthetic_market(n_symbols=args.symbols, n_days=args.days, seed=args.seed,
                                momentum_strength=args.momentum)
    if not args.data:
        sys.exit("either --data DIR or --synthetic is required")
    return load_directory(args.data)


def _data_args(p):
    p.add_argument("--data", help="directory with bars.csv [actions.csv] [benchmark.csv]")
    p.add_argument("--synthetic", action="store_true", help="use a SYNTHETIC market (pipeline testing only)")
    p.add_argument("--symbols", type=int, default=30)
    p.add_argument("--days", type=int, default=1500)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--momentum", type=float, default=0.0, help="planted momentum strength for synthetic data")


def _parse_params(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_validate(args):
    from .data.validation import validate

    md = _load(args)
    rep = validate(md)
    print(json.dumps({"data_version": md.version(), "ok": rep.ok, "summary": rep.summary()}, indent=2))
    if args.out:
        rep.to_frame().to_csv(args.out, index=False)
    return 0 if rep.ok else 2


def cmd_costs(args):
    from .costs.model import CostModel

    m = CostModel(args.broker)
    d = pd.Timestamp(args.date)
    buy, sell = m.charges("BUY", args.value, d), m.charges("SELL", args.value, d)
    print(json.dumps({"schedule": m.version_id(d), "buy": buy.as_dict(), "sell": sell.as_dict(),
                      "round_trip_pct": round(100 * m.round_trip_fraction(args.value, d), 3)}, indent=2))
    return 0


def cmd_backtest(args):
    from .backtest.engine import BacktestConfig, run_backtest
    from .stats.metrics import estimate_stcg_tax, summarize
    from .strategies.library import REGISTRY

    md = _load(args)
    if args.strategy not in REGISTRY:
        sys.exit(f"unknown strategy; choose from {sorted(REGISTRY)}")
    cfg = BacktestConfig(initial_cash=args.cash, broker=args.broker)
    res = run_backtest(REGISTRY[args.strategy](**_parse_params(args.param)), md, cfg)
    s = summarize(res)
    s["costs"] = res.costs
    s["est_stcg_tax_by_fy"] = estimate_stcg_tax(res.trades).to_dict()
    s["data_version"], s["cost_version"] = res.data_version, res.cost_version
    print(json.dumps(s, indent=2, default=str))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        res.equity.to_csv(out / "equity.csv")
        res.fills.to_csv(out / "fills.csv", index=False)
        res.trades.to_csv(out / "trades.csv", index=False)
        res.events.to_csv(out / "events.csv", index=False)
        (out / "summary.json").write_text(json.dumps(s, indent=2, default=str))
    return 0


def cmd_tournament(args):
    from .backtest.engine import BacktestConfig
    from .research.protocol import ResearchSplit
    from .research.registry import Registry
    from .research.tournament import run_tournament, write_report

    md = _load(args)
    if args.holdout_start:
        md = ResearchSplit(pd.Timestamp(args.holdout_start)).research(md)
        print(f"holdout locked from {args.holdout_start}; researching on data up to {md.dates[-1].date()}")
    reg = Registry(args.registry)
    out = run_tournament(md, cfg=BacktestConfig(initial_cash=args.cash, broker=args.broker), registry=reg,
                         train_days=args.train, test_days=args.test)
    path = write_report(out, args.out, synthetic=bool(md.meta.get("synthetic")))
    print(path.read_text())
    return 0


def cmd_paper(args):
    from .backtest.engine import BacktestConfig
    from .paper.session import PaperSession
    from .strategies.library import REGISTRY

    md = _load(args)
    if args.strategy not in REGISTRY:
        sys.exit(f"unknown strategy; choose from {sorted(REGISTRY)}")
    ps = PaperSession(args.state, REGISTRY[args.strategy](**_parse_params(args.param)),
                      BacktestConfig(initial_cash=args.cash, broker=args.broker))
    recs = ps.run_until(md, pd.Timestamp(args.date) if args.date else None)
    if args.backup:
        ps.backup(Path(args.backup) / pd.Timestamp.now().strftime("%Y%m%d-%H%M%S"))
    if not recs:
        print("nothing to do: all sessions up to the requested date were already processed")
        return 0
    last = recs[-1]
    print(json.dumps({"sessions_processed": len(recs), "last_session": last["date"], "equity": round(last["equity"], 2),
                      "cash": round(last["cash"], 2), "positions": last["positions"],
                      "orders_for_next_open": last["submitted"], "risk_rejected": last["risk_rejected"],
                      "halted": last["halted"], "alerts": [e for r in recs for e in r["events"]
                                                           if "ALERT" in e["kind"] or "rejected" in e["kind"]]},
                     indent=2, default=str))
    return 3 if last["halted"] else 0


def cmd_registry(args):
    from .research.registry import Registry

    reg = Registry(args.registry)
    print(json.dumps({"verified": reg.verify(), "records": len(reg.records()), "trials": reg.n_trials(),
                      "holdout_accesses": len(reg.holdout_accesses())}, indent=2))
    return 0 if reg.verify() else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="omega", description="OMEGA QUANT research CLI (paper/research only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="run data-quality checks")
    _data_args(p)
    p.add_argument("--out", help="write issues CSV")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("costs", help="show explicit charges for one order value")
    p.add_argument("--value", type=float, default=2500.0)
    p.add_argument("--date", default=str(pd.Timestamp.today().date()))
    p.add_argument("--broker", default="discount_zero_delivery")
    p.set_defaults(fn=cmd_costs)

    p = sub.add_parser("backtest", help="backtest one strategy")
    _data_args(p)
    p.add_argument("--strategy", required=True)
    p.add_argument("--param", action="append", help="k=v strategy parameter (repeatable)")
    p.add_argument("--cash", type=float, default=5000.0)
    p.add_argument("--broker", default="discount_zero_delivery")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("tournament", help="walk-forward tournament of all Tier-1 strategies")
    _data_args(p)
    p.add_argument("--holdout-start", help="lock data on/after this date (never seen by research)")
    p.add_argument("--registry", default="runs/registry.jsonl")
    p.add_argument("--cash", type=float, default=5000.0)
    p.add_argument("--broker", default="discount_zero_delivery")
    p.add_argument("--train", type=int, default=504)
    p.add_argument("--test", type=int, default=126)
    p.add_argument("--out", default="results/tournament")
    p.set_defaults(fn=cmd_tournament)

    p = sub.add_parser("paper", help="run the daily PAPER-trading session(s) up to the latest data")
    _data_args(p)
    p.add_argument("--strategy", required=True)
    p.add_argument("--param", action="append", help="k=v strategy parameter (repeatable)")
    p.add_argument("--state", default="runs/paper", help="state directory (SQLite, broker file, audit log)")
    p.add_argument("--date", help="process sessions up to this date (default: last bar)")
    p.add_argument("--cash", type=float, default=5000.0)
    p.add_argument("--broker", default="discount_zero_delivery")
    p.add_argument("--backup", help="directory for a timestamped state backup after the run")
    p.set_defaults(fn=cmd_paper)

    p = sub.add_parser("registry", help="verify the experiment registry hash chain")
    p.add_argument("--registry", default="runs/registry.jsonl")
    p.set_defaults(fn=cmd_registry)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
