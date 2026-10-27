"""Data source adapters.

Only file-based adapters ship in this version: the operator downloads data from
an authorised source (NSE bhavcopy archives, a broker's historical API export, a
licensed vendor) and OMEGA ingests the files. Network adapters are deliberately
absent from the core so that research runs are reproducible from versioned files.

See docs/DATA_SOURCES.md for the assessment of each source.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .market import FIELDS, MarketData, from_long

# Column maps for NSE equity bhavcopy formats. Column names are taken from
# publicly circulated sample files and MUST be verified against a freshly
# downloaded file before production use (see ASSUMPTIONS.md, A-D3).
_BHAV_LEGACY = {
    "TIMESTAMP": "date",
    "SYMBOL": "symbol",
    "SERIES": "series",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "TOTTRDQTY": "volume",
}
_BHAV_UDIFF = {
    "TradDt": "date",
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "TtlTradgVol": "volume",
}


def read_bars_csv(path: str | Path) -> pd.DataFrame:
    """Generic long-format CSV: date,symbol,open,high,low,close,volume."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    return df[["date", "symbol", *FIELDS]]


def read_bhavcopy(path: str | Path, series: tuple[str, ...] = ("EQ",)) -> pd.DataFrame:
    """Parse one NSE cash-market bhavcopy (legacy or UDiFF layout) to long bars."""
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    if set(_BHAV_UDIFF) <= set(df.columns):
        cmap = _BHAV_UDIFF
    elif set(_BHAV_LEGACY) <= set(df.columns):
        cmap = _BHAV_LEGACY
    else:
        raise ValueError(f"unrecognised bhavcopy layout in {path}: {list(df.columns)[:12]}")
    out = df[list(cmap)].rename(columns=cmap)
    if "ISIN" in df.columns:  # present in both layouts (UNVERIFIED for every historical year)
        out["isin"] = df["ISIN"].astype(str).str.strip().str.upper()
    out["series"] = out["series"].astype(str).str.strip()
    out = out[out["series"].isin(series)].drop(columns="series")
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["date"] = pd.to_datetime(out["date"], format="mixed", dayfirst=False).dt.normalize()
    return out.reset_index(drop=True)


def read_actions_csv(path: str | Path) -> pd.DataFrame:
    """symbol,ex_date,kind,value  (kind in {split, dividend}; split value = share multiplier)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["ex_date"] = pd.to_datetime(df["ex_date"])
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["kind"] = df["kind"].astype(str).str.strip().str.lower()
    return df[["symbol", "ex_date", "kind", "value"]]


def load_directory(path: str | Path) -> MarketData:
    """Load ``bars.csv`` (+ optional ``actions.csv``, ``benchmark.csv``) from a directory."""
    p = Path(path)
    bars = read_bars_csv(p / "bars.csv")
    actions = read_actions_csv(p / "actions.csv") if (p / "actions.csv").exists() else None
    bench = None
    if (p / "benchmark.csv").exists():
        b = pd.read_csv(p / "benchmark.csv", parse_dates=["date"]).set_index("date")["close"]
        bench = b
    md = from_long(bars, actions, source=str(p))
    if bench is not None:
        bench = bench.reindex(md.dates)
        md = MarketData(md.raw, md.adj, md.actions, bench, md.source, md.meta)
    return md


# --------------------------------------------------------------------------- synthetic


def synthetic_market(
    n_symbols: int = 30,
    n_days: int = 1500,
    *,
    seed: int = 7,
    momentum_strength: float = 0.0,
    start: str = "2015-01-01",
    price_range: tuple[float, float] = (40.0, 3000.0),
    with_actions: bool = True,
    missing_rate: float = 0.002,
    delist: int = 1,
) -> MarketData:
    """Generate a SYNTHETIC market for testing the pipeline. NOT real data.

    Returns follow a one-factor model with regime-switching market volatility:
        r_{i,t} = beta_i * m_t + mu_i,t + e_{i,t}
    ``momentum_strength`` > 0 plants a persistent drift: each stock's drift
    mu_i,t is an AR(1) process, so past winners keep winning. With 0 the market
    is a null (no predictable cross-sectional structure) - used to verify that
    the anti-overfitting machinery does not report spurious significance.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    regime = np.zeros(n_days, dtype=int)
    for t in range(1, n_days):
        stay = 0.995 if regime[t - 1] == 0 else 0.98
        regime[t] = regime[t - 1] if rng.random() < stay else 1 - regime[t - 1]
    mvol = np.where(regime == 0, 0.009, 0.022)
    mdrift = np.where(regime == 0, 0.0004, -0.0008)
    m = mdrift + mvol * rng.standard_normal(n_days)
    beta = rng.uniform(0.6, 1.4, n_symbols)
    idio = rng.uniform(0.010, 0.025, n_symbols)
    mu = np.zeros((n_days, n_symbols))
    if momentum_strength > 0:
        phi = 0.995
        # stationary drift std = strength * 0.0006/day (~15%/yr cross-sectional dispersion at strength 1)
        eps = rng.standard_normal((n_days, n_symbols)) * momentum_strength * 0.0006 * np.sqrt(1 - phi**2)
        for t in range(1, n_days):
            mu[t] = phi * mu[t - 1] + eps[t]
    r = m[:, None] * beta[None, :] + mu + idio[None, :] * rng.standard_normal((n_days, n_symbols))
    r = np.clip(r, -0.18, 0.18)
    p0 = np.exp(rng.uniform(np.log(price_range[0]), np.log(price_range[1]), n_symbols))
    close = p0[None, :] * np.exp(np.cumsum(r, axis=0))
    gap = 0.004 * rng.standard_normal((n_days, n_symbols))
    open_ = np.empty_like(close)
    open_[0] = p0
    open_[1:] = close[:-1] * np.exp(gap[1:])
    hi = np.maximum(open_, close) * np.exp(np.abs(0.006 * rng.standard_normal((n_days, n_symbols))))
    lo = np.minimum(open_, close) * np.exp(-np.abs(0.006 * rng.standard_normal((n_days, n_symbols))))
    vol = np.round(rng.lognormal(12, 0.6, (n_days, n_symbols)) / np.sqrt(p0[None, :] / 100))
    symbols = [f"SYN{i:03d}" for i in range(n_symbols)]
    frames = {
        "open": open_,
        "high": hi,
        "low": lo,
        "close": close,
        "volume": vol,
    }
    frames = {k: pd.DataFrame(v, index=dates, columns=symbols) for k, v in frames.items()}

    actions = []
    if with_actions and n_symbols >= 2 and n_days > 300:
        # A 2-for-1 split and a dividend applied to the RAW series.
        s, t0 = symbols[0], n_days // 2
        for f in ("open", "high", "low", "close"):
            frames[f].iloc[t0:, 0] /= 2.0
        frames["volume"].iloc[t0:, 0] *= 2.0
        actions.append({"symbol": s, "ex_date": dates[t0], "kind": "split", "value": 2.0})
        s1, t1 = symbols[1], n_days // 3
        prev = float(frames["close"].iloc[t1 - 1, 1])
        dps = round(prev * 0.02, 2)
        for f in ("open", "high", "low", "close"):
            frames[f].iloc[t1:, 1] *= 1.0 - dps / prev  # ex-dividend price drop
        actions.append({"symbol": s1, "ex_date": dates[t1], "kind": "dividend", "value": dps})

    if missing_rate > 0:
        miss = rng.random((n_days, n_symbols)) < missing_rate
        miss[0] = False
        for f in frames:
            frames[f] = frames[f].mask(miss)
    for k in range(min(delist, n_symbols)):
        cut = int(n_days * (0.6 + 0.1 * k))
        for f in frames:
            frames[f].iloc[cut:, -(k + 1)] = np.nan

    long = pd.concat({f: frames[f].stack() for f in FIELDS}, axis=1).reset_index()
    long.columns = ["date", "symbol", *FIELDS]
    act_df = pd.DataFrame(actions) if actions else None
    bench = pd.Series(1000.0 * np.exp(np.cumsum(m + 0.00005)), index=dates, name="SYN_INDEX_TR")
    md = from_long(long, act_df, source=f"SYNTHETIC(seed={seed},mom={momentum_strength})")
    return MarketData(md.raw, md.adj, md.actions, bench, md.source, {"synthetic": True, "seed": seed})
