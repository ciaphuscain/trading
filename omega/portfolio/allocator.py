"""Whole-share, cost-aware conversion of target weights into order intents."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..costs.model import CostModel
from ..strategies.base import BookView, Target


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # BUY | SELL
    qty: int
    reason: str
    stop_frac: float | None = None
    ref_price: float = 0.0  # raw decision-time price used for sizing


@dataclass(frozen=True)
class AllocatorConfig:
    cash_buffer: float = 0.02           # keep for overnight gaps + charges
    min_order_value: float = 500.0      # INR; below this fixed charges dominate
    rebalance_band: float = 0.25        # resize a held position only if off-target by >25% of target
    max_round_trip_cost: float = 0.02   # skip entries whose estimated round trip costs > 2%
    slippage_bps: float = 5.0           # used in cost estimates only


def allocate(target: Target, book: BookView, prices: dict[str, float], when: pd.Timestamp,
             costs: CostModel, cfg: AllocatorConfig = AllocatorConfig()) -> list[OrderIntent]:
    """Translate a Target into whole-share orders. Sells are listed before buys.

    Buys are sized conservatively against cash projected after sells, so that the
    next-open execution rarely needs to shrink an order for insufficient cash.
    """
    if target.no_change:
        return []
    sells: list[OrderIntent] = []
    buys: list[OrderIntent] = []
    proj_cash = book.cash
    equity = book.equity

    for s, h in book.holdings.items():
        if s not in target.weights:
            px = prices[s]
            sells.append(OrderIntent(s, "SELL", h.qty, target.reasons.get(s, target.reasons.get("*", "exit")), ref_price=px))
            proj_cash += h.qty * px - costs.charges("SELL", h.qty * px, when).total

    def affordable_qty(budget: float, px: float) -> int:
        unit = px * (1 + cfg.slippage_bps / 1e4)
        q = int(budget // unit)
        while q > 0 and q * unit + costs.charges("BUY", q * unit, when).total > budget:
            q -= 1
        return q

    resized: list[tuple[str, float]] = []
    for s, w in target.weights.items():
        if w is None:
            continue
        if not (0.0 <= w <= 1.0):
            raise ValueError(f"weight for {s} out of [0,1]: {w}")
        resized.append((s, w))
    if sum(w for _, w in resized) > 1.0 + 1e-9:
        raise ValueError("target weights sum above 1 (leverage is disabled)")

    for s, w in sorted(resized, key=lambda x: (-x[1], x[0])):
        px = prices.get(s)
        if px is None or not math.isfinite(px) or px <= 0:
            continue
        want_value = w * equity * (1 - cfg.cash_buffer)
        held = book.holdings[s].qty if s in book.holdings else 0
        want = affordable_qty(want_value, px)
        delta = want - held
        if held and abs(delta) * px <= cfg.rebalance_band * want_value:
            continue
        if delta < 0:
            sells.append(OrderIntent(s, "SELL", -delta, "rebalance_down", ref_price=px))
            proj_cash += -delta * px - costs.charges("SELL", -delta * px, when).total
        elif delta > 0:
            buys.append(OrderIntent(s, "BUY", delta, target.reasons.get(s, "rebalance_up" if held else "entry"),
                                    target.stops.get(s), ref_price=px))

    out = list(sells)
    for o in buys:
        q = min(o.qty, affordable_qty(proj_cash * (1 - cfg.cash_buffer / 2), o.ref_price))
        value = q * o.ref_price
        if q <= 0 or value < cfg.min_order_value:
            continue
        if costs.round_trip_fraction(value, when, cfg.slippage_bps) > cfg.max_round_trip_cost:
            continue
        proj_cash -= value + costs.charges("BUY", value, when).total
        out.append(OrderIntent(o.symbol, "BUY", q, o.reason, o.stop_frac, o.ref_price))
    return out
