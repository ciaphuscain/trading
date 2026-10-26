"""Simple, causal market-regime labels and per-regime performance.

Deliberately the simplest option from the specification (fixed thresholds): trend = market
index above/below its 200-session SMA; volatility = 63-session realised volatility above/below
its own EXPANDING median (only past data). More complex detectors (HMM, change points) are not
justified until these show that regimes matter for a candidate.

Labels are known at the close of day t and are attached to the return earned on day t+1 when
analysing performance (no look-ahead in the attribution either).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..data.market import MarketData


def market_index(data: MarketData) -> pd.Series:
    """Benchmark if supplied, else an equal-weight index of adjusted closes."""
    if data.benchmark is not None and data.benchmark.notna().sum() > 10:
        return data.benchmark.ffill()
    r = data.adj.close.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    return (1 + r).cumprod()


def market_regimes(data: MarketData, trend_n: int = 200, vol_n: int = 63) -> pd.DataFrame:
    idx = market_index(data)
    sma = idx.rolling(trend_n, min_periods=trend_n).mean()
    trend = np.where(sma.isna(), "warmup", np.where(idx > sma, "up", "down"))
    vol = np.log(idx).diff().rolling(vol_n, min_periods=vol_n).std() * math.sqrt(252)
    med = vol.expanding(min_periods=vol_n).median()
    vlab = np.where(vol.isna() | med.isna(), "warmup", np.where(vol > med, "high_vol", "low_vol"))
    return pd.DataFrame({"trend": trend, "vol": vlab}, index=data.dates)


def regime_table(returns: pd.Series, regimes: pd.DataFrame) -> pd.DataFrame:
    """Annualised return / Sharpe of ``returns`` by the regime known at the previous close."""
    lab = regimes.shift(1).reindex(returns.index)
    rows = []
    for col in ("trend", "vol"):
        for name, r in returns.groupby(lab[col]):
            if name == "warmup" or len(r) < 2:
                continue
            sd = r.std(ddof=1)
            rows.append({"dimension": col, "regime": name, "days": int(len(r)),
                         "ann_return": float((1 + r).prod() ** (252 / len(r)) - 1),
                         "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0})
    return pd.DataFrame(rows)
