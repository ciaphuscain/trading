"""NSE cash-market bhavcopy archive downloader (operator-run).

* Read NSE's website terms of use before running; keep the default rate limit or slower.
* URL patterns below are UNVERIFIED (ASSUMPTIONS A-D3/A-D7): NSE has changed archive hosts
  and file layouts before (legacy ``cmDDMONYYYYbhav.csv.zip``; UDiFF ``BhavCopy_NSE_CM_...``
  from mid-2024). If a pattern stops working, update ``bhavcopy_urls`` and its test.
* Files are cached immutably; a re-run only fetches missing days. A 404 on a weekday is
  recorded as a probable holiday so it is not re-requested every run.
* This module was NOT exercised against the live site from the build environment (the
  network policy blocked nseindia.com); it is tested with a fake fetcher.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from .sources import read_bhavcopy

UDIFF_START = pd.Timestamp("2024-07-08")  # UNVERIFIED switch date to the UDiFF layout


def bhavcopy_urls(day: pd.Timestamp) -> list[str]:
    """Candidate URLs for one session, most likely first."""
    d = pd.Timestamp(day)
    udiff = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    legacy = f"https://archives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
    return [udiff, legacy] if d >= UDIFF_START else [legacy, udiff]


def default_fetch(url: str, timeout: float = 30.0) -> bytes | None:
    """HTTP GET; returns None for 404 (no file for that day). Other errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (OMEGA research; personal use)",
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URLs
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_range(start, end, cache_dir: str | Path, *, fetch=default_fetch, min_interval: float = 2.0,
                   sleep=time.sleep) -> dict:
    """Fetch every weekday's bhavcopy in [start, end] into ``cache_dir`` (skips cached days)."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    hol_path = cache / "no_file_days.json"
    no_file = set(json.loads(hol_path.read_text())) if hol_path.exists() else set()
    stats = {"fetched": 0, "cached": 0, "no_file": 0, "requests": 0}
    last = None
    for d in pd.bdate_range(start, end):
        key = f"{d:%Y-%m-%d}"
        target = cache / f"bhav_{d:%Y%m%d}.csv.zip"
        if target.exists():
            stats["cached"] += 1
            continue
        if key in no_file:
            stats["no_file"] += 1
            continue
        data = None
        for url in bhavcopy_urls(d):
            if last is not None:
                wait = min_interval - (time.monotonic() - last)
                if wait > 0:
                    sleep(wait)
            last = time.monotonic()
            stats["requests"] += 1
            data = fetch(url)
            if data is not None:
                break
        if data is None:
            no_file.add(key)
            stats["no_file"] += 1
            continue
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValueError(f"{key}: response is not a zip archive (blocked or changed endpoint?)")
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        stats["fetched"] += 1
    hol_path.write_text(json.dumps(sorted(no_file)))
    return stats


def build_bars(cache_dir: str | Path, series: tuple[str, ...] = ("EQ",)) -> pd.DataFrame:
    """Concatenate cached bhavcopies into canonical long bars (date,symbol,open,high,low,close,volume)."""
    frames = []
    for f in sorted(Path(cache_dir).glob("bhav_*.csv.zip")):
        with zipfile.ZipFile(f) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(name) as fh:
                tmp = io.BytesIO(fh.read())
        frames.append(read_bhavcopy(tmp, series=series))
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    bars = pd.concat(frames, ignore_index=True)
    if "isin" in bars.columns:
        bars = unify_symbols_by_isin(bars).drop(columns="isin")
    return bars.sort_values(["date", "symbol"]).reset_index(drop=True)


def unify_symbols_by_isin(bars: pd.DataFrame) -> pd.DataFrame:
    """Give every ISIN one symbol across its whole history: the LATEST ticker it traded under.

    NSE tickers change on renames (e.g. after a corporate restructuring) while the ISIN stays,
    so without this a renamed stock looks like a delisting plus a new listing. If the latest
    ticker of one ISIN was used earlier by a different ISIN (ticker reuse), the older instrument
    is suffixed ``~<ISIN>`` so two securities are never merged. Rows without an ISIN keep
    their ticker. Note: a change of ISIN itself (e.g. some mergers) is NOT bridged.
    """
    b = bars.copy()
    has = b["isin"].notna() & (b["isin"] != "") & (b["isin"] != "NAN")
    latest = b[has].sort_values("date").groupby("isin")["symbol"].last()
    owner_dates = b[has].groupby("isin")["date"].max()
    taken: dict[str, str] = {}
    for isin in owner_dates.sort_values(ascending=False).index:  # most recently active claims first
        sym = latest[isin]
        taken[isin] = sym if sym not in taken.values() else f"{sym}~{isin}"
    b.loc[has, "symbol"] = b.loc[has, "isin"].map(taken)
    if b.duplicated(["date", "symbol"]).any():
        raise ValueError("ISIN unification produced duplicate (date, symbol) bars; inspect the source files")
    return b
