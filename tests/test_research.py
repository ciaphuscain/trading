import json

import pandas as pd
import pytest

from omega.backtest.engine import run_backtest
from omega.data.sources import synthetic_market
from omega.research.protocol import GateConfig, ResearchSplit, grid, promotion_gate, walk_forward
from omega.research.registry import Registry
from omega.research.tournament import run_tournament, write_report
from omega.strategies.library import CrossSectionalMomentum, REGISTRY


def test_registry_hash_chain_detects_tampering(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    for k in range(3):
        reg.record_trial(family="f", strategy="s", params={"k": k}, data_version="d", cost_version="c",
                         metrics={"sharpe": -1.0 * k}, period_sr=0.01 * k)
    assert reg.verify() and reg.n_trials("f") == 3
    lines = (tmp_path / "reg.jsonl").read_text().splitlines()
    # Concealing a failed experiment (deleting a line) breaks the chain.
    (tmp_path / "reg.jsonl").write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not reg.verify()
    # Editing a result breaks the hash.
    rec = json.loads(lines[1])
    rec["metrics"]["sharpe"] = 3.0
    (tmp_path / "reg.jsonl").write_text("\n".join([lines[0], json.dumps(rec, sort_keys=True), lines[2]]) + "\n")
    assert not reg.verify()


def test_holdout_is_locked_and_access_is_counted():
    md = synthetic_market(n_symbols=6, n_days=400, seed=4)
    split = ResearchSplit(md.dates[300])
    research = split.research(md)
    assert research.dates[-1] < md.dates[300]
    reg = Registry(None)
    split.open_holdout(md, reg, "M1", "final evaluation")
    with pytest.raises(PermissionError):
        split.open_holdout(md, reg, "M1", "peek again")
    split.open_holdout(md, reg, "M1", "explicit re-test", allow_repeat=True)
    assert len(reg.holdout_accesses("M1")) == 2


def test_window_rebuilds_point_in_time_adjustment():
    md = synthetic_market(n_symbols=4, n_days=1000, seed=6)
    w = md.window(md.dates[600], None)  # after the synthetic split at row 500
    r_full = md.adj.close.pct_change(fill_method=None).iloc[601:]
    r_win = w.adj.close.pct_change(fill_method=None).iloc[1:]
    pd.testing.assert_frame_equal(r_full, r_win, check_freq=False)


def test_walk_forward_records_every_trial_and_stitches_oos():
    md = synthetic_market(n_symbols=10, n_days=700, seed=8, momentum_strength=2.0)
    reg = Registry(None)
    g = grid(lookback=[40, 80], skip=[0, 5], top_n=[2])
    wf = walk_forward("M1", CrossSectionalMomentum, g, md, train_days=300, test_days=100, registry=reg)
    assert reg.n_trials("M1") == len(g) == 4
    assert len(wf.folds) == 4
    assert wf.oos_returns.index.min() == md.dates[300]
    assert wf.oos_returns.index.is_monotonic_increasing and not wf.oos_returns.index.has_duplicates
    for _, f in wf.folds.iterrows():
        assert f.train_start < f.test_start


def test_promotion_gate_rejects_weak_evidence():
    ev = {"years": 3.0, "dsr": 0.4, "sharpe_ci95": [-0.2, 1.1], "pbo": 0.6, "n_trades": 12, "max_drawdown": 0.4,
          "total_return": -0.01, "benchmark_total_return": 0.2, "n_trials_charged": 50}
    decision, reasons = promotion_gate(ev, GateConfig())
    assert decision == "REJECT" and len(reasons) == 7
    strong = {"years": 4.0, "dsr": 0.99, "sharpe_ci95": [0.3, 1.5], "pbo": 0.1, "n_trades": 80, "max_drawdown": 0.2,
              "total_return": 0.6, "benchmark_total_return": 0.3, "n_trials_charged": 50}
    assert promotion_gate(strong)[0] == "PROMOTE_TO_PAPER"


def test_tournament_on_null_market_promotes_nothing(tmp_path):
    """On a market with no planted structure the honest answer is 'hold cash'."""
    md = synthetic_market(n_symbols=12, n_days=900, seed=21, momentum_strength=0.0)
    small = {"M1_xs_momentum": grid(lookback=[63, 126], skip=[0, 21], top_n=[2]),
             "B1_donchian_breakout": grid(entry_n=[20, 55], exit_n=[10, 20])}
    out = run_tournament(md, grids=small, train_days=300, test_days=150)
    assert all(d == "REJECT" for d, _ in out["decisions"].values())
    assert "hold cash" in out["recommendation"]
    p = write_report(out, tmp_path, synthetic=True)
    assert "SYNTHETIC DATA" in p.read_text()


def test_cash_strategy_is_exactly_flat():
    md = synthetic_market(n_symbols=5, n_days=300, seed=1)
    r = run_backtest(REGISTRY["C1_cash"](), md)
    assert (r.equity == 5000.0).all() and r.fills.empty
