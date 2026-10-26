"""Causal technical indicators on wide (dates x symbols) frames.

Every function uses only rows <= t to produce row t (rolling windows, shifts >= 0,
ewm with adjust=True). tests/test_lookahead.py verifies truncation invariance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def total_return(close: pd.DataFrame, lookback: int, skip: int = 0) -> pd.DataFrame:
    """Return from t-lookback to t-skip (e.g. 12-1 momentum: lookback=252, skip=21)."""
    return close.shift(skip) / close.shift(lookback) - 1.0


def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def realized_vol(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return np.log(close).diff().rolling(n, min_periods=n).std() * np.sqrt(252)


def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder RSI using an exponential average with alpha = 1/n."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = up / dn
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(dn > 0, 100.0).where(d.notna().cumsum() >= n)


def true_range(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    pc = close.shift(1)
    a = high - low
    b = (high - pc).abs()
    c = (low - pc).abs()
    # fmax ignores the NaN previous close on a symbol's first bar (TR = high - low).
    return np.fmax(np.fmax(a, b), c)


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int) -> pd.DataFrame:
    return true_range(high, low, close).rolling(n, min_periods=n).mean()


def rolling_max(x: pd.DataFrame, n: int, exclude_today: bool = True) -> pd.DataFrame:
    y = x.shift(1) if exclude_today else x
    return y.rolling(n, min_periods=n).max()


def rolling_min(x: pd.DataFrame, n: int, exclude_today: bool = True) -> pd.DataFrame:
    y = x.shift(1) if exclude_today else x
    return y.rolling(n, min_periods=n).min()


def zscore(x: pd.DataFrame, n: int) -> pd.DataFrame:
    m = x.rolling(n, min_periods=n).mean()
    s = x.rolling(n, min_periods=n).std()
    return (x - m) / s


def median_traded_value(close: pd.DataFrame, volume: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return (close * volume).rolling(n, min_periods=max(5, n // 2)).median()
