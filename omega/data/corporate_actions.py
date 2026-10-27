"""Parse NSE corporate-action exports into OMEGA's ``actions.csv`` format.

Input: the CSV downloadable from NSE's corporate-actions page (columns as recalled:
SYMBOL, COMPANY NAME, SERIES, PURPOSE, FACE VALUE, EX-DATE, RECORD DATE, ...). The
column names and PURPOSE phrasings are UNVERIFIED (ASSUMPTIONS A-D8): every row that is
not understood is returned in ``unparsed`` rather than guessed, and rights issues,
demergers and schemes of arrangement are deliberately not modelled (reported instead).

Output kinds: ``split`` (value = share multiplier; bonus a:b -> (a+b)/b; face-value split
from F1 to F2 -> F1/F2; consolidation gives a multiplier < 1) and ``dividend`` (INR per
share; multiple dividends in one PURPOSE are summed).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_NUM = r"(\d+(?:\.\d+)?)"
_DIV = re.compile(r"dividend\s*-?\s*(?:rs|re|inr)\.?\s*" + _NUM + r"\s*/?-?\s*per\s*(?:equity\s*)?share", re.I)
_BONUS = re.compile(r"bonus\s*(?:issue)?\s*-?\s*" + _NUM + r"\s*:\s*" + _NUM, re.I)
_FV = re.compile(r"from\s*(?:rs|re|inr)\.?\s*" + _NUM + r"\s*/?-?.*?to\s*(?:rs|re|inr)\.?\s*" + _NUM, re.I)
_UNSUPPORTED = re.compile(r"rights|demerger|scheme of arrangement|amalgamation|buy\s*back|capital reduction", re.I)


def parse_purpose(purpose: str) -> tuple[list[tuple[str, float]], str | None]:
    """Return ([(kind, value), ...], problem) for one PURPOSE string."""
    p = " ".join(str(purpose).split())
    out: list[tuple[str, float]] = []
    divs = [float(x) for x in _DIV.findall(p)]
    if divs:
        out.append(("dividend", round(sum(divs), 4)))
    if m := _BONUS.search(p):
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            out.append(("split", (a + b) / b))
    if re.search(r"split|sub-?division|consolidation", p, re.I):
        m = _FV.search(p)
        if m and float(m.group(2)) > 0:
            out.append(("split", float(m.group(1)) / float(m.group(2))))
        else:
            return out, f"split without parseable face values: {p!r}"
    if _UNSUPPORTED.search(p):
        return out, f"unsupported action (not modelled): {p!r}"
    if not out and not re.search(r"annual general meeting|agm|egm|interest|redemption", p, re.I):
        return out, f"not understood: {p!r}"
    return out, None


def parse_nse_actions(path: str | Path, series: tuple[str, ...] = ("EQ",)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (actions[symbol, ex_date, kind, value], unparsed[symbol, ex_date, purpose, problem])."""
    df = pd.read_csv(path)
    df.columns = [c.strip().upper() for c in df.columns]
    need = {"SYMBOL", "PURPOSE", "EX-DATE"}
    if not need <= set(df.columns):
        raise ValueError(f"unrecognised corporate-action layout in {path}: {list(df.columns)}")
    if "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip().isin(series)]
    rows, bad = [], []
    for r in df.itertuples(index=False):
        sym = str(getattr(r, "SYMBOL")).strip().upper()
        ex = pd.to_datetime(str(r[list(df.columns).index("EX-DATE")]), errors="coerce", dayfirst=True)
        purpose = r[list(df.columns).index("PURPOSE")]
        if pd.isna(ex):
            bad.append({"symbol": sym, "ex_date": None, "purpose": purpose, "problem": "invalid ex-date"})
            continue
        acts, problem = parse_purpose(purpose)
        rows.extend({"symbol": sym, "ex_date": ex.normalize(), "kind": k, "value": v} for k, v in acts)
        if problem:
            bad.append({"symbol": sym, "ex_date": ex.normalize(), "purpose": purpose, "problem": problem})
    actions = pd.DataFrame(rows, columns=["symbol", "ex_date", "kind", "value"])
    if len(actions):
        # Two rows for the same split (e.g. duplicated announcements) must not double-adjust.
        actions = actions.drop_duplicates().sort_values(["ex_date", "symbol"]).reset_index(drop=True)
    return actions, pd.DataFrame(bad, columns=["symbol", "ex_date", "purpose", "problem"])
