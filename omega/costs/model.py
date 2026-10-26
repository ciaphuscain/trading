"""Transaction cost engine for NSE delivery trades.

Explicit charges come from a dated YAML schedule (omega/config/). Implicit costs
(half-spread + impact) are modelled separately by :class:`SlippageModel` because they
are market estimates, not tariffs.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

DEFAULT_SCHEDULE = Path(__file__).resolve().parent.parent / "config" / "charges_nse_equity_delivery.yaml"


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi_fee: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    dp: float = 0.0

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange_txn + self.sebi_fee + self.stamp + self.gst + self.dp, 2)

    def as_dict(self) -> dict[str, float]:
        d = {k: getattr(self, k) for k in ("brokerage", "stt", "exchange_txn", "sebi_fee", "stamp", "gst", "dp")}
        d["total"] = self.total
        return d


def _versions(rows: list[dict]) -> tuple[list[pd.Timestamp], list[dict]]:
    rows = sorted(rows, key=lambda r: r["effective_from"])
    return [pd.Timestamp(r["effective_from"]) for r in rows], rows


def _r2(x: float) -> float:
    # Round half up to paise; float-safe enough for INR amounts well below 1e9.
    return math.floor(x * 100 + 0.5 + 1e-9) / 100


class CostModel:
    """Explicit charges for a given broker profile, resolved by trade date."""

    def __init__(self, broker: str = "discount_zero_delivery", schedule_path: str | Path | None = None):
        path = Path(schedule_path) if schedule_path else DEFAULT_SCHEDULE
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        if broker not in cfg["brokers"]:
            raise KeyError(f"unknown broker profile {broker!r}; have {sorted(cfg['brokers'])}")
        self.broker = broker
        self.schedule_path = str(path)
        self._stat_dates, self._stat = _versions(cfg["statutory"])
        self._brk_dates, self._brk = _versions(cfg["brokers"][broker])

    @staticmethod
    def _pick(dates: list[pd.Timestamp], rows: list[dict], when: pd.Timestamp) -> dict:
        i = bisect.bisect_right(dates, pd.Timestamp(when)) - 1
        if i < 0:
            raise ValueError(f"no charge schedule in force on {pd.Timestamp(when).date()}")
        return rows[i]

    def version_id(self, when: pd.Timestamp) -> str:
        s = self._pick(self._stat_dates, self._stat, when)
        b = self._pick(self._brk_dates, self._brk, when)
        return f"stat@{s['effective_from']}/{self.broker}@{b['effective_from']}"

    def charges(self, side: str, value: float, when: pd.Timestamp, *, first_sell_of_scrip_today: bool = True) -> CostBreakdown:
        """Explicit charges for ONE executed order of traded ``value`` INR.

        DP charges are levied once per scrip per day on the sell side, independent
        of quantity - for a INR 5,000 account this is often the largest single cost.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(side)
        if value < 0:
            raise ValueError("negative traded value")
        if value == 0:
            return CostBreakdown()
        s = self._pick(self._stat_dates, self._stat, when)
        b = self._pick(self._brk_dates, self._brk, when)
        brokerage = value * b["brokerage_pct"]
        if b["brokerage_flat"] > 0:
            brokerage = min(brokerage, b["brokerage_flat"]) if b["brokerage_pct"] > 0 else b["brokerage_flat"]
        if b.get("brokerage_cap", 0) > 0:
            brokerage = min(brokerage, b["brokerage_cap"])
        stt_rate = s["stt_buy"] if side == "BUY" else s["stt_sell"]
        stt = value * stt_rate
        stt = float(math.floor(stt + 0.5)) if s.get("stt_rounding") == "rupee" else stt  # half-up, not banker's
        exch = value * s["exchange_txn"]
        sebi = value * s["sebi_fee"]
        stamp = value * s["stamp_buy"] if side == "BUY" else 0.0
        dp = b["dp_per_scrip_sell_day"] if (side == "SELL" and first_sell_of_scrip_today) else 0.0
        gst = (brokerage + exch + sebi + dp) * s["gst"]
        return CostBreakdown(_r2(brokerage), _r2(stt), _r2(exch), _r2(sebi), _r2(stamp), _r2(gst), _r2(dp))

    def round_trip_fraction(self, value: float, when: pd.Timestamp, slippage_bps: float = 0.0) -> float:
        """Estimated round-trip cost as a fraction of ``value`` (buy then sell same value)."""
        if value <= 0:
            return math.inf
        c = self.charges("BUY", value, when).total + self.charges("SELL", value, when).total
        return c / value + 2 * slippage_bps / 1e4


@dataclass(frozen=True)
class SlippageModel:
    """Implicit cost: price moves against us by half-spread + square-root impact.

    fill = ref * (1 + side * (half_spread_bps + impact_coef * sqrt(qty / adv)) / 1e4)
    Defaults are assumptions for liquid NSE large/mid caps and are varied in
    sensitivity analysis; they are NOT measured from order-book data.
    """

    half_spread_bps: float = 5.0
    impact_coef_bps: float = 10.0
    max_participation: float = 0.02  # of the fill day's volume

    def price(self, ref: float, side: str, qty: int, day_volume: float) -> float:
        sign = 1.0 if side == "BUY" else -1.0
        part = qty / day_volume if day_volume and day_volume > 0 else 1.0
        bps = self.half_spread_bps + self.impact_coef_bps * math.sqrt(max(part, 0.0))
        return ref * (1.0 + sign * bps / 1e4)

    def bps_estimate(self) -> float:
        return self.half_spread_bps
