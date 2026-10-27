"""Paper-trading session: equivalence with the backtester, restartability, crash recovery."""

import sqlite3

import numpy as np
import pytest

from omega.backtest.engine import BacktestConfig, run_backtest
from omega.costs.model import SlippageModel
from omega.data.sources import synthetic_market
from omega.paper.session import PaperSession
from omega.strategies.library import REGISTRY

MD = synthetic_market(n_symbols=10, n_days=380, seed=5, momentum_strength=2.0)
CFG = BacktestConfig(slippage=SlippageModel(max_participation=1.0))  # paper broker has no volume cap
PARAMS = {"M1_xs_momentum": dict(lookback=60, skip=5, rebalance=10),
          "M2_ts_trend": dict(fast=20, slow=60, abs_lookback=40),
          "R1_cond_mean_reversion": dict(trend_n=60),
          "B1_donchian_breakout": dict(entry_n=20, exit_n=10)}


def _paper(tmp, name, data=MD, date=None):
    ps = PaperSession(tmp, REGISTRY[name](**PARAMS[name]), CFG)
    ps.run_until(data, date)
    return ps


@pytest.mark.parametrize("name", list(PARAMS))
def test_paper_session_reproduces_backtest_equity(tmp_path, name):
    """Same data, same rules: the daily paper job (order manager + paper broker + reconciliation)
    must produce exactly the backtester's equity curve. This comparison found a real backtester
    bug (risk start-of-day equity on split ex-dates)."""
    bt = run_backtest(REGISTRY[name](**PARAMS[name]), MD, CFG)
    with _paper(tmp_path, name) as ps:
        hist = ps.history()
    np.testing.assert_allclose(hist.equity.to_numpy(), bt.equity.to_numpy(), rtol=0, atol=1e-6)


def test_rerun_is_noop_and_restart_continues(tmp_path):
    with _paper(tmp_path / "a", "B1_donchian_breakout") as ref:
        full = ref.history()
    with _paper(tmp_path / "b", "B1_donchian_breakout", date=MD.dates[200]) as ps:
        assert ps.run_until(MD, MD.dates[200]) == []  # same day again: nothing to do
    with _paper(tmp_path / "b", "B1_donchian_breakout") as ps2:  # new process, same state dir
        h = ps2.history()
    np.testing.assert_allclose(h.equity.to_numpy(), full.equity.to_numpy(), atol=1e-6)


def test_crash_before_end_of_day_commit_recovers_without_duplicates(tmp_path):
    name = "M2_ts_trend"
    with _paper(tmp_path / "ref", name) as ref:
        full = ref.history()
    ps = PaperSession(tmp_path / "s", REGISTRY[name](**PARAMS[name]), CFG)
    crash_day = str(MD.dates[150].date())
    real = ps._persist

    def crashing(rec):
        if rec["date"] == crash_day:
            raise SystemExit("power failure after orders were sent, before state commit")
        real(rec)

    ps._persist = crashing
    with pytest.raises(SystemExit):
        ps.run_until(MD)
    ps.close()
    ps2 = _paper(tmp_path / "s", name)
    h = ps2.history()
    np.testing.assert_allclose(h.equity.to_numpy(), full.equity.to_numpy(), atol=1e-6)
    # every client order id exists once at the broker and once locally
    assert len(ps2.broker.orders) == len({o.client_order_id for o in ps2.manager.orders()})
    assert not ps2.manager.halted
    ps2.close()


def test_kill_switch_file_blocks_new_buys(tmp_path):
    ps = PaperSession(tmp_path, REGISTRY["B1_donchian_breakout"](**PARAMS["B1_donchian_breakout"]), CFG)
    (tmp_path / "KILL_SWITCH").write_text("operator stop")
    ps.run_until(MD)
    h = ps.history()
    ps.close()
    assert all(s["side"] != "BUY" for subs in h.submitted for s in subs)
    assert any(r["reason"] == "kill_switch" for rr in h.risk_rejected for r in rr)


def test_backup_and_integrity_check(tmp_path):
    ps = _paper(tmp_path / "s", "M1_xs_momentum", date=MD.dates[120])
    dest = ps.backup(tmp_path / "bk")
    assert {"session.sqlite", "orders.sqlite", "paper_broker.json"} <= {p.name for p in dest.iterdir()}
    ps.close()
    (tmp_path / "s" / "session.sqlite").write_bytes(b"this is not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        PaperSession(tmp_path / "s", REGISTRY["M1_xs_momentum"](), CFG)


def test_paper_matches_backtest_on_circuit_locked_sessions(tmp_path):
    from omega.strategies.base import Target

    from .helpers import W, Scripted, make_bars, permissive_eligibility

    bars = [(100, 100, 100, 100, 1e6), (105, 105, 105, 105, 1e6), (106, 107, 105, 106, 1e6),
            (106, 107, 105, 106, 1e6), (95, 95, 95, 95, 1e6), (96, 97, 95, 96, 1e6), (97, 98, 96, 97, 1e6)]
    md = make_bars({"AAA": bars})
    plan = {W: Target(weights={"AAA": 0.5}), W + 1: Target(weights={"AAA": 0.5}), W + 3: Target(weights={}),
            W + 4: Target(weights={})}
    mk = lambda: Scripted(plan, eligibility=permissive_eligibility())  # noqa: E731
    bt = run_backtest(mk(), md, CFG)
    with PaperSession(tmp_path, mk(), CFG) as ps:
        ps.run_until(md)
        eq = ps.history().equity.to_numpy()
    np.testing.assert_allclose(eq, bt.equity.to_numpy(), atol=1e-6)
    assert bt.fills.side.tolist() == ["BUY", "SELL"]
