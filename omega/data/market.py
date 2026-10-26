"""Canonical in-memory market data model.

Two price bases exist and are kept in separate, explicitly-labelled containers:

* ``RAW`` - prices exactly as traded on the exchange. The simulator fills, sizes
  and marks positions ONLY on raw prices.
* ``ADJUSTED`` - forward-adjusted total-return prices. Strategies compute signals
  ONLY on adjusted prices.

Forward adjustment (factor F_t = product of adjustment factors of all corporate
actions with ex-date <= t) is used instead of the more common back adjustment
because it is strictly point-in-time: the adjusted price on day t never changes
when later data (and later corporate actions) arrive. Back-adjusted series
silently rewrite history, which breaks truncation-invariance tests.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

FIELDS = ("open", "high", "low", "close", "volume")


class PriceBasis(str, Enum):
    RAW = "raw"
    ADJUSTED = "adjusted"


@dataclass(frozen=True)
class PriceFrame:
    """Wide (dates x symbols) OHLCV frames on a single declared price basis."""

    basis: PriceBasis
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame

    def field(self, name: str) -> pd.DataFrame:
        if name not in FIELDS:
            raise KeyError(name)
        return getattr(self, name)

    def iloc_rows(self, stop: int) -> "PriceFrame":
        return PriceFrame(self.basis, *(self.field(f).iloc[:stop] for f in FIELDS))

    def require(self, basis: PriceBasis) -> "PriceFrame":
        """Guard against silently mixing adjusted and unadjusted prices."""
        if self.basis is not basis:
            raise ValueError(f"expected {basis.value} prices, got {self.basis.value}")
        return self


@dataclass(frozen=True)
class MarketData:
    """Point-in-time market data for a universe of symbols.

    ``actions`` columns: symbol, ex_date, kind ('split'|'dividend'), value.
    For splits/bonuses ``value`` is the share multiplier (2-for-1 split -> 2.0,
    1:1 bonus -> 2.0). For dividends it is INR per share.
    """

    raw: PriceFrame
    adj: PriceFrame
    actions: pd.DataFrame
    benchmark: pd.Series | None = None  # e.g. Nifty 50 TRI; never tradable
    source: str = "unknown"
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.raw.require(PriceBasis.RAW)
        self.adj.require(PriceBasis.ADJUSTED)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.raw.close.index  # type: ignore[return-value]

    @property
    def symbols(self) -> list[str]:
        return list(self.raw.close.columns)

    def __len__(self) -> int:
        return len(self.dates)

    def truncate(self, stop: int) -> "MarketData":
        """Return the data visible at the close of row ``stop - 1``."""
        last = self.dates[stop - 1]
        acts = self.actions[self.actions["ex_date"] <= last] if len(self.actions) else self.actions
        bench = self.benchmark.iloc[:stop] if self.benchmark is not None else None
        return MarketData(self.raw.iloc_rows(stop), self.adj.iloc_rows(stop), acts, bench, self.source, dict(self.meta))

    def window(self, start: pd.Timestamp | None, end: pd.Timestamp | None) -> "MarketData":
        """Calendar slice [start, end]. Adjusted prices are rebuilt so that the
        slice is identical to what would have been seen had data started at ``start``
        (forward factors are rescaled; ratios are unchanged)."""
        idx = self.dates
        mask = np.ones(len(idx), dtype=bool)
        if start is not None:
            mask &= idx >= start
        if end is not None:
            mask &= idx <= end
        sl = lambda df: df.loc[mask]  # noqa: E731
        raw = PriceFrame(PriceBasis.RAW, *(sl(self.raw.field(f)) for f in FIELDS))
        acts = self.actions
        if len(acts):
            keep = np.ones(len(acts), dtype=bool)
            if start is not None:
                keep &= acts["ex_date"] >= start
            if end is not None:
                keep &= acts["ex_date"] <= end
            acts = acts[keep]
        bench = self.benchmark.loc[mask] if self.benchmark is not None else None
        return build_market_data(raw, acts, bench, self.source, dict(self.meta))

    def version(self) -> str:
        """Content hash identifying this exact dataset (prices + actions + benchmark).

        Values are canonicalised (dates as ISO strings, numbers rounded to 1e-6) so the
        hash is stable across storage formats (CSV vs Parquet) and pandas dtype changes.
        """
        h = hashlib.sha256()
        h.update("|".join(d.strftime("%Y-%m-%d") for d in self.dates).encode())
        h.update("|".join(map(str, self.symbols)).encode())
        for f in FIELDS:
            vals = np.round(self.raw.field(f).to_numpy(dtype=float), 6)
            h.update(f.encode())
            h.update(np.nan_to_num(vals, nan=-1.0).tobytes())
        for a in self.actions.itertuples(index=False):
            h.update(f"{a.symbol}|{pd.Timestamp(a.ex_date).date()}|{a.kind}|{float(a.value):.6f}".encode())
        if self.benchmark is not None:
            h.update(np.nan_to_num(np.round(self.benchmark.to_numpy(dtype=float), 6), nan=-1.0).tobytes())
        return h.hexdigest()[:16]


def empty_actions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype=str),
            "ex_date": pd.Series(dtype="datetime64[ns]"),
            "kind": pd.Series(dtype=str),
            "value": pd.Series(dtype=float),
        }
    )


def adjustment_factors(raw_close: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Forward total-return adjustment factors F (dates x symbols).

    adj_price_t = raw_price_t * F_t, with F_t = prod over actions with ex_date <= t of
      * split with multiplier m:            m
      * dividend D with previous close C:  C / (C - D)
    so adjusted returns equal total returns (dividends reinvested at the ex-date close).
    """
    factors = pd.DataFrame(1.0, index=raw_close.index, columns=raw_close.columns)
    if actions is None or len(actions) == 0:
        return factors
    prev_close = raw_close.ffill().shift(1)
    step = pd.DataFrame(1.0, index=raw_close.index, columns=raw_close.columns)
    for row in actions.itertuples(index=False):
        if row.symbol not in step.columns:
            continue
        # Effective on the first trading day on or after ex_date.
        pos = raw_close.index.searchsorted(pd.Timestamp(row.ex_date))
        if pos >= len(raw_close.index):
            continue
        d = raw_close.index[pos]
        if row.kind == "split":
            if row.value <= 0:
                raise ValueError(f"invalid split multiplier {row.value} for {row.symbol}")
            step.loc[d, row.symbol] *= float(row.value)
        elif row.kind == "dividend":
            c = prev_close.loc[d, row.symbol]
            if not np.isfinite(c) or c <= row.value:
                continue  # cannot adjust without a valid previous close; flagged by validation
            step.loc[d, row.symbol] *= c / (c - float(row.value))
        else:
            raise ValueError(f"unknown corporate action kind {row.kind!r}")
    return step.cumprod()


def build_market_data(
    raw: PriceFrame,
    actions: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    source: str = "unknown",
    meta: dict | None = None,
) -> MarketData:
    raw.require(PriceBasis.RAW)
    actions = empty_actions() if actions is None else actions.copy()
    if len(actions):
        actions["ex_date"] = pd.to_datetime(actions["ex_date"])
        actions = actions.sort_values(["ex_date", "symbol"]).reset_index(drop=True)
    f = adjustment_factors(raw.close, actions)
    adj = PriceFrame(
        PriceBasis.ADJUSTED,
        raw.open * f,
        raw.high * f,
        raw.low * f,
        raw.close * f,
        raw.volume / f,
    )
    return MarketData(raw, adj, actions, benchmark, source, meta or {})


def from_long(bars: pd.DataFrame, actions: pd.DataFrame | None = None, **kw) -> MarketData:
    """Build MarketData from long-format bars: date, symbol, open, high, low, close, volume.

    Missing (date, symbol) combinations stay NaN, meaning "no bar: not tradable".
    """
    need = {"date", "symbol", *FIELDS}
    missing = need - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")
    b = bars.copy()
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    if b.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate (date, symbol) bars; run validation and deduplicate first")
    frames = {f: b.pivot(index="date", columns="symbol", values=f).sort_index().astype(float) for f in FIELDS}
    cols = sorted(frames["close"].columns)
    frames = {f: df.reindex(columns=cols) for f, df in frames.items()}
    raw = PriceFrame(PriceBasis.RAW, *(frames[f] for f in FIELDS))
    return build_market_data(raw, actions, **kw)
