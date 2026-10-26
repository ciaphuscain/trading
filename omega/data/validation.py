"""Deterministic data-quality checks.

Every check returns issues with a severity. ERROR issues make a dataset unfit for
research until fixed; WARN issues are recorded in the experiment metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .market import FIELDS, MarketData


@dataclass(frozen=True)
class Issue:
    severity: str  # "ERROR" | "WARN"
    check: str
    symbol: str | None
    date: pd.Timestamp | None
    detail: str


@dataclass
class QualityReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "WARN"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            key = f"{i.severity}:{i.check}"
            out[key] = out.get(key, 0) + 1
        return out

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([i.__dict__ for i in self.issues])


def validate_long_bars(bars: pd.DataFrame) -> QualityReport:
    """Checks that must run before pivoting long-format bars (duplicates, schema)."""
    rep = QualityReport()
    need = {"date", "symbol", *FIELDS}
    missing = need - set(bars.columns)
    if missing:
        rep.issues.append(Issue("ERROR", "schema", None, None, f"missing columns {sorted(missing)}"))
        return rep
    dup = bars[bars.duplicated(["date", "symbol"], keep=False)]
    for (d, s), g in dup.groupby(["date", "symbol"]):
        conflicting = len(g[list(FIELDS)].drop_duplicates()) > 1
        rep.issues.append(
            Issue("ERROR", "duplicate_bar", s, pd.Timestamp(d), "conflicting values" if conflicting else "exact duplicate")
        )
    return rep


def validate(
    data: MarketData,
    *,
    max_abs_return: float = 0.35,
    stale_run: int = 5,
    max_gap_sessions: int = 10,
    as_of: pd.Timestamp | None = None,
) -> QualityReport:
    """Validate a MarketData object.

    max_abs_return: NSE price bands for most stocks are <= 20%; a raw close-to-close
      move larger than this without a recorded corporate action usually means an
      unrecorded split/bonus. Flagged ERROR because it corrupts returns.
    """
    rep = QualityReport()
    raw = data.raw
    o, h, l, c, v = (raw.field(f) for f in FIELDS)
    idx = data.dates

    if not idx.is_monotonic_increasing or idx.has_duplicates:
        rep.issues.append(Issue("ERROR", "date_order", None, None, "dates not strictly increasing"))
    if as_of is not None and len(idx) and idx[-1] > as_of:
        rep.issues.append(Issue("ERROR", "future_date", None, idx[-1], f"bar dated after as_of {as_of.date()}"))
    weekend = idx[idx.dayofweek >= 5]
    for d in weekend:
        # NSE has held special weekend sessions (e.g. Budget days, Muhurat), so WARN only.
        rep.issues.append(Issue("WARN", "weekend_session", None, d, "bar on a weekend"))

    present = c.notna()
    # Partial bars: some fields present and others missing.
    partial = (o.notna() | h.notna() | l.notna() | v.notna()) & ~present
    _emit(rep, partial, "ERROR", "partial_bar", "fields missing within a bar")

    bad_hl = present & ((h < l) | (h < o) | (h < c) | (l > o) | (l > c))
    _emit(rep, bad_hl, "ERROR", "ohlc_inconsistent", "high/low do not bracket open/close")
    nonpos = present & ((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0))
    _emit(rep, nonpos, "ERROR", "non_positive_price", "price <= 0")
    negvol = present & (v < 0)
    _emit(rep, negvol, "ERROR", "negative_volume", "volume < 0")

    # Large raw moves not explained by a corporate action on that date.
    ret = c / c.ffill().shift(1) - 1.0
    explained = pd.DataFrame(False, index=idx, columns=c.columns)
    for row in data.actions.itertuples(index=False):
        if row.symbol in explained.columns:
            pos = idx.searchsorted(pd.Timestamp(row.ex_date))
            if pos < len(idx):
                explained.iloc[pos, explained.columns.get_loc(row.symbol)] = True
    jump = present & (ret.abs() > max_abs_return) & ~explained
    _emit(rep, jump, "ERROR", "unexplained_jump", f"|close return| > {max_abs_return:.0%} without corporate action")

    # Stale prices: identical OHLC and zero volume repeated.
    flat = present & (o == c) & (h == c) & (l == c) & (v == 0)
    for s in c.columns:
        fs = flat[s]
        r = fs.astype(int).groupby((~fs).cumsum()).cumsum()
        hits = r[r == stale_run]
        for d in hits.index:
            rep.issues.append(Issue("WARN", "stale_price", s, d, f"{stale_run} flat zero-volume bars"))

    # Gaps inside a symbol's listed life (between first and last bar).
    for s in c.columns:
        col = present[s]
        if not col.any():
            rep.issues.append(Issue("WARN", "no_data", s, None, "symbol has no bars"))
            continue
        first, last = col.idxmax(), col[::-1].idxmax()
        life = col.loc[first:last]
        gap = (~life).astype(int).groupby(life.cumsum()).sum()
        for _, n in gap[gap > 0].items():
            sev = "WARN" if n <= max_gap_sessions else "ERROR"
            rep.issues.append(Issue(sev, "missing_sessions", s, None, f"{int(n)} consecutive missing sessions"))
        if last < idx[-1]:
            rep.issues.append(Issue("WARN", "series_ends_early", s, last, "possible delisting/suspension"))

    for row in data.actions.itertuples(index=False):
        if row.symbol not in c.columns:
            rep.issues.append(Issue("WARN", "action_unknown_symbol", row.symbol, row.ex_date, row.kind))
        if row.kind not in ("split", "dividend"):
            rep.issues.append(Issue("ERROR", "action_kind", row.symbol, row.ex_date, str(row.kind)))
        elif not np.isfinite(row.value) or row.value <= 0:
            rep.issues.append(Issue("ERROR", "action_value", row.symbol, row.ex_date, str(row.value)))
    return rep


def _emit(rep: QualityReport, mask: pd.DataFrame, sev: str, check: str, detail: str, limit: int = 200) -> None:
    stacked = mask.stack()
    hits = stacked[stacked]
    for n, (d, s) in enumerate(hits.index):
        if n >= limit:
            rep.issues.append(Issue(sev, check, None, None, f"... {len(hits) - limit} more"))
            break
        rep.issues.append(Issue(sev, check, s, d, detail))
