"""Strategy interface.

Contract (enforced by tests/test_lookahead.py for every registered strategy):

* ``prepare(data)`` may precompute indicators over the whole frame, but each row t
  may depend only on rows <= t (causal). Signals read ADJUSTED prices; affordability
  checks read RAW prices (the price you actually pay).
* ``decide(i, book)`` is called after the close of row ``i`` and returns the target
  portfolio to be executed at the next session's open. It must not read row i+1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.market import MarketData
from . import indicators as ind


@dataclass(frozen=True)
class Holding:
    qty: int
    entry_index: int
    avg_cost: float


@dataclass(frozen=True)
class BookView:
    """Read-only view of the portfolio given to strategies."""

    equity: float
    cash: float
    holdings: dict[str, Holding]


@dataclass
class Target:
    """weights: symbol -> fraction of equity (new/resized position) or None (keep as is).
    Held symbols absent from ``weights`` are exited. ``no_change`` skips all trading.
    stops: symbol -> stop distance as a fraction below the decision close (raw)."""

    weights: dict[str, float | None] = field(default_factory=dict)
    stops: dict[str, float] = field(default_factory=dict)
    no_change: bool = False
    reasons: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def hold() -> "Target":
        return Target(no_change=True)


@dataclass(frozen=True)
class Eligibility:
    """Universe filters applied at decision time with point-in-time data."""

    min_median_traded_value: float = 5e6  # INR/day, 20-day median
    min_price: float = 10.0               # avoid penny stocks (raw INR)
    cash_buffer: float = 0.02


class Strategy(ABC):
    name: str = "abstract"
    warmup: int = 0

    def __init__(self, **params):
        params = dict(params)
        self.elig: Eligibility = params.pop("eligibility", None) or Eligibility()
        self.params = params
        self._data: MarketData | None = None

    def describe(self) -> dict:
        return {"strategy": self.name, **self.params}

    def prepare(self, data: MarketData) -> None:
        self._data = data
        a, r = data.adj, data.raw
        self._has_bar = r.close.notna().to_numpy()
        self._raw_close = r.close.to_numpy()
        self._liq = ind.median_traded_value(r.close, r.volume, 20).to_numpy()
        self._symbols = np.array(data.symbols)
        self._sym_idx = {s: j for j, s in enumerate(data.symbols)}
        self._prepare(data)

    @abstractmethod
    def _prepare(self, data: MarketData) -> None: ...

    @abstractmethod
    def decide(self, i: int, book: BookView) -> Target: ...

    # helpers ---------------------------------------------------------------
    def eligible(self, i: int, equity: float, slots: int) -> np.ndarray:
        """Boolean mask of symbols tradable and affordable at the close of row i."""
        px = self._raw_close[i]
        budget = equity * (1 - self.elig.cash_buffer) / max(slots, 1)
        with np.errstate(invalid="ignore"):
            ok = (
                self._has_bar[i]
                & (self._liq[i] >= self.elig.min_median_traded_value)
                & (px >= self.elig.min_price)
                & (px <= budget)
            )
        return np.nan_to_num(ok, nan=0).astype(bool)


def top_k(scores: np.ndarray, mask: np.ndarray, k: int) -> list[int]:
    """Indices of the k highest finite scores among mask, deterministic tie-break by index."""
    idx = np.where(mask & np.isfinite(scores))[0]
    if len(idx) == 0 or k <= 0:
        return []
    order = idx[np.lexsort((idx, -scores[idx]))]
    return list(order[:k])


def equal_weights(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    w = 1.0 / len(symbols)
    return {s: w for s in symbols}


def filled(x: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    """Causal forward fill over short gaps (missing sessions) for indicator input."""
    return x.ffill(limit=limit)
