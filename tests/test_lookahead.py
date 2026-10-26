"""Truncation-invariance (no look-ahead) tests.

If a signal at row t changes when rows > t are deleted, the signal used future data.
"""

import numpy as np
import pytest

from omega.data.sources import synthetic_market
from omega.strategies import indicators as ind
from omega.strategies.base import BookView
from omega.strategies.library import REGISTRY

from .helpers import permissive_eligibility

MD = synthetic_market(n_symbols=10, n_days=420, seed=9, momentum_strength=1.0)
SMALL = {"M1_xs_momentum": dict(lookback=60, skip=5, rebalance=7, trend_filter=50),
         "M2_ts_trend": dict(fast=20, slow=60, abs_lookback=40),
         "R1_cond_mean_reversion": dict(trend_n=60),
         "B1_donchian_breakout": dict(entry_n=20, exit_n=10, volume_mult=1.2)}


def test_adjusted_prices_are_point_in_time():
    full = MD.adj.close
    for t in (150, 210, 300, 419):
        trunc = MD.truncate(t + 1).adj.close
        np.testing.assert_allclose(trunc.to_numpy(), full.iloc[: t + 1].to_numpy(), rtol=1e-12, equal_nan=True)


@pytest.mark.parametrize("fn", [
    lambda c, h, l, v: ind.total_return(c, 60, 5),
    lambda c, h, l, v: ind.rsi(c, 2),
    lambda c, h, l, v: ind.atr(h, l, c, 14),
    lambda c, h, l, v: ind.rolling_max(h, 20),
    lambda c, h, l, v: ind.zscore(c, 20),
    lambda c, h, l, v: ind.realized_vol(c, 20),
    lambda c, h, l, v: ind.median_traded_value(c, v, 20),
])
def test_indicators_truncation_invariant(fn):
    a = MD.adj
    full = fn(a.close, a.high, a.low, a.volume).to_numpy()
    for t in (100, 250, 400):
        tr = MD.truncate(t + 1).adj
        part = fn(tr.close, tr.high, tr.low, tr.volume).to_numpy()
        np.testing.assert_allclose(part[t], full[t], rtol=1e-9, equal_nan=True)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_strategy_decisions_truncation_invariant(name):
    el = permissive_eligibility()
    book = BookView(equity=5000.0, cash=5000.0, holdings={})
    s_full = REGISTRY[name](eligibility=el, **SMALL.get(name, {}))
    s_full.prepare(MD)
    for t in range(120, 419, 23):
        s_tr = REGISTRY[name](eligibility=el, **SMALL.get(name, {}))
        s_tr.prepare(MD.truncate(t + 1))
        a, b = s_full.decide(t, book), s_tr.decide(t, book)
        assert a.weights == b.weights and a.no_change == b.no_change
        assert a.stops.keys() == b.stops.keys()
        for k in a.stops:
            assert a.stops[k] == pytest.approx(b.stops[k])
