import numpy as np
import pandas as pd
import pytest

from omega.backtest.engine import BacktestConfig, run_backtest
from omega.costs.model import CostModel, SlippageModel
from omega.data.sources import synthetic_market
from omega.portfolio.allocator import AllocatorConfig
from omega.risk.engine import RiskLimits
from omega.strategies.base import Target
from omega.strategies.library import REGISTRY

from .helpers import W, Scripted, flat, make_bars, permissive_eligibility

NO_SLIP = SlippageModel(half_spread_bps=0, impact_coef_bps=0, max_participation=1.0)
LOOSE_RISK = RiskLimits(max_position_fraction=1.0, max_participation=1.0, max_loss_per_trade_fraction=1.0)
ALLOC = AllocatorConfig(cash_buffer=0.0, min_order_value=0.0, max_round_trip_cost=1.0, slippage_bps=0.0)
CFG = BacktestConfig(slippage=NO_SLIP, risk=LOOSE_RISK, allocator=ALLOC)


def test_hand_calculated_trade_next_open_execution():
    # Decide at close of day 0, buy at OPEN of day 1 (101), sell decided day 2 -> fill at open day 3 (110).
    md = make_bars({"AAA": [(100, 100, 100, 100, 1e6), (101, 106, 100, 105, 1e6),
                            (105, 109, 104, 108, 1e6), (110, 111, 109, 110, 1e6), (110, 110, 110, 110, 1e6)]})
    plan = {W: Target(weights={"AAA": 0.5}), W + 2: Target(weights={})}
    r = run_backtest(Scripted(plan, eligibility=permissive_eligibility()), md, CFG)
    f = r.fills
    assert list(f.side) == ["BUY", "SELL"]
    assert f.price.tolist() == [101.0, 110.0]
    qty = int(f.qty.iloc[0])
    # sizing uses the decision close (100): floor(2500 / 100) = 25 minus charges headroom -> 24
    assert qty == 24
    cm = CostModel()
    buy_fee = cm.charges("BUY", qty * 101.0, md.dates[W + 1]).total
    sell_fee = cm.charges("SELL", qty * 110.0, md.dates[W + 3]).total
    expected = 5000 - qty * 101 - buy_fee + qty * 110 - sell_fee
    assert r.equity.iloc[-1] == pytest.approx(expected)
    assert r.fills.fees.tolist() == [buy_fee, sell_fee]


def test_gap_through_stop_fills_at_open_not_stop():
    bars = [(100, 100, 100, 100, 1e6), (100, 101, 99, 100, 1e6), (80, 82, 78, 81, 1e6), (81, 81, 81, 81, 1e6)]
    md = make_bars({"AAA": bars})
    plan = {W: Target(weights={"AAA": 0.5}, stops={"AAA": 0.05})}  # stop at 95
    r = run_backtest(Scripted(plan, eligibility=permissive_eligibility()), md, CFG)
    sell = r.fills[r.fills.side == "SELL"].iloc[0]
    assert sell.price == 80.0 and sell.reason == "stop_gap"


def test_order_for_missing_bar_is_rejected_not_filled():
    # BBB trades every session so the calendar includes the day AAA has no bar.
    md = make_bars({"AAA": [(100, 100, 100, 100, 1e6), None, (100, 100, 100, 100, 1e6)], "BBB": flat(10, 3)})
    r = run_backtest(Scripted({W: Target(weights={"AAA": 0.5})}, eligibility=permissive_eligibility()), md, CFG)
    assert r.fills.empty
    assert (r.events.kind == "order_rejected").any()


def test_split_keeps_equity_continuous_and_dividend_credited():
    pre = [(100, 100, 100, 100, 1e6)] * 3
    post = [(50, 50, 50, 50, 2e6)] * 3
    acts = pd.DataFrame({"symbol": ["AAA", "AAA"], "ex_date": pd.bdate_range("2024-12-30", periods=W + 6)[[W + 3, W + 5]],
                         "kind": ["split", "dividend"], "value": [2.0, 1.0]})
    md = make_bars({"AAA": pre + post}, actions=acts)
    r = run_backtest(Scripted({W: Target(weights={"AAA": 0.5})}, eligibility=permissive_eligibility()), md, CFG)
    q = int(r.fills.qty.iloc[0])
    eq = r.equity
    assert eq.iloc[W + 3] == pytest.approx(eq.iloc[W + 2])  # split: no value change
    assert eq.iloc[W + 5] - eq.iloc[W + 4] == pytest.approx(2 * q * 1.0)  # dividend on doubled share count
    assert r.costs["dividends"] == pytest.approx(2 * q)


def test_cash_only_never_negative_and_no_leverage():
    md = synthetic_market(n_symbols=12, n_days=400, seed=3)
    for cls in REGISTRY.values():
        r = run_backtest(cls(), md)
        assert (r.cash >= -1e-6).all()
        assert (r.exposure <= 1.0 + 1e-9).all()
        if len(r.fills):
            assert (r.fills.qty == r.fills.qty.astype(int)).all()


def test_deterministic_replay():
    md = synthetic_market(n_symbols=15, n_days=500, seed=11, momentum_strength=1.0)
    a = run_backtest(REGISTRY["M1_xs_momentum"](), md)
    b = run_backtest(REGISTRY["M1_xs_momentum"](), md)
    pd.testing.assert_series_equal(a.equity, b.equity)
    pd.testing.assert_frame_equal(a.fills, b.fills)


@pytest.mark.parametrize("name", [n for n in REGISTRY if n != "C1_cash"])
def test_fast_engine_matches_strict_point_in_time_reference(name):
    """The vectorised prepare() path must give the same equity curve as re-preparing the
    strategy on data truncated at every session (the slow reference)."""
    md = synthetic_market(n_symbols=8, n_days=320, seed=5, momentum_strength=1.0)
    params = {"M1_xs_momentum": dict(lookback=60, skip=5, rebalance=10),
              "M2_ts_trend": dict(fast=20, slow=60, abs_lookback=40),
              "R1_cond_mean_reversion": dict(trend_n=60),
              "B1_donchian_breakout": dict(entry_n=20, exit_n=10)}.get(name, {})
    el = permissive_eligibility()
    fast = run_backtest(REGISTRY[name](eligibility=el, **params), md)
    slow = run_backtest(REGISTRY[name](eligibility=el, **params), md, BacktestConfig(strict_pit=True))
    np.testing.assert_allclose(fast.equity.to_numpy(), slow.equity.to_numpy(), rtol=1e-9)


def test_risk_engine_can_veto_everything():
    md = synthetic_market(n_symbols=10, n_days=300, seed=2)
    veto = RiskLimits(max_position_fraction=0.0)
    r = run_backtest(REGISTRY["B1_donchian_breakout"](entry_n=20), md, BacktestConfig(risk=veto))
    assert r.fills.empty
    assert (r.risk_log.reason == "max_position_fraction").all() if len(r.risk_log) else True


def test_unaffordable_buy_at_open_is_rejected_not_shrunk():
    # Price gaps up 50% overnight; the order sized at the close cannot be afforded. Like a broker's
    # risk system, the engine rejects it (it does not silently buy fewer shares).
    # (the gap-up bar trades in a range, so it is not a circuit lock)
    md = make_bars({"AAA": [(100, 100, 100, 100, 1e6), (150, 152, 148, 150, 1e6), (150, 152, 148, 150, 1e6)]})
    r = run_backtest(Scripted({W: Target(weights={"AAA": 1.0})}, eligibility=permissive_eligibility()), md, CFG)
    assert r.fills.empty
    assert (r.cash >= 0).all()
    rej = r.events[r.events.kind == "order_rejected"]
    assert rej.reason.tolist() == ["insufficient_cash_at_open"]


def test_delisted_position_is_written_off():
    bars = [(100, 100, 100, 100, 1e6)] * 3 + [None] * 12
    md = make_bars({"AAA": bars, "BBB": flat(10, 15)})
    cfg = BacktestConfig(slippage=NO_SLIP, risk=LOOSE_RISK, allocator=ALLOC, delist_after=10, delist_recovery=0.5)
    r = run_backtest(Scripted({W: Target(weights={"AAA": 0.5})}, eligibility=permissive_eligibility()), md, cfg)
    assert (r.events.kind == "write_off").any()
    q = int(r.fills.qty.iloc[0])
    assert r.trades.iloc[-1].proceeds == pytest.approx(q * 100 * 0.5)


def test_circuit_locked_sessions_cannot_be_traded_against():
    # Day W+1 opens locked at the upper circuit (H == L, gap up): the buy cannot fill.
    # The position bought later cannot be sold into a lower-circuit lock either.
    bars = [(100, 100, 100, 100, 1e6), (105, 105, 105, 105, 1e6), (106, 107, 105, 106, 1e6),
            (106, 107, 105, 106, 1e6), (95, 95, 95, 95, 1e6), (96, 97, 95, 96, 1e6)]
    md = make_bars({"AAA": bars})
    plan = {W: Target(weights={"AAA": 0.5}), W + 1: Target(weights={"AAA": 0.5}), W + 3: Target(weights={})}
    r = run_backtest(Scripted(plan, eligibility=permissive_eligibility()), md, CFG)
    rej = r.events[r.events.kind == "order_rejected"]
    assert rej.reason.tolist() == ["circuit_locked", "circuit_locked"]
    assert r.fills.side.tolist() == ["BUY"]  # bought on W+2; the sell on W+4 was blocked
    assert r.fills.date.iloc[0] == md.dates[W + 2]
