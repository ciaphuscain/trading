"""Small hand-built datasets and a scripted strategy for exact-arithmetic tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from omega.data.market import MarketData, from_long
from omega.strategies.base import BookView, Strategy, Target


W = 5  # warm-up sessions prepended so the risk engine has volume history


def make_bars(spec: dict[str, list[tuple]], start="2024-12-30", actions=None, warm: int = W) -> MarketData:
    """spec: symbol -> list of (open, high, low, close, volume) or None (no bar), one per session.

    ``warm`` flat copies of each symbol's first bar are prepended, so scripted plans
    should index decisions as W + k."""
    spec = {s: [b[0] if b[0] is not None else None] * warm + list(b) for s, b in spec.items()}
    n = len(next(iter(spec.values())))
    dates = pd.bdate_range(start, periods=n)
    rows = []
    for s, bars in spec.items():
        for d, b in zip(dates, bars):
            if b is not None:
                o, h, l, c, v = b
                rows.append({"date": d, "symbol": s, "open": o, "high": h, "low": l, "close": c, "volume": v})
    return from_long(pd.DataFrame(rows), actions, source="test")


def flat(price: float, n: int, vol: float = 1e6) -> list[tuple]:
    return [(price, price, price, price, vol)] * n


class Scripted(Strategy):
    """Returns pre-programmed targets: plan[i] -> Target (missing -> hold)."""

    name = "scripted"

    def __init__(self, plan: dict[int, Target], **kw):
        super().__init__(**kw)
        self.plan = plan

    def _prepare(self, data):
        pass

    def decide(self, i: int, book: BookView) -> Target:
        return self.plan.get(i, Target.hold())


def permissive_eligibility():
    from omega.strategies.base import Eligibility

    return Eligibility(min_median_traded_value=0.0, min_price=0.0, cash_buffer=0.0)


def random_walk_frame(rng: np.random.Generator, n: int, k: int) -> np.ndarray:
    return 100 * np.exp(np.cumsum(0.02 * rng.standard_normal((n, k)), axis=0))
