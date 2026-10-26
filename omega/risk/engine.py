"""Independent, deterministic pre-trade risk engine.

The risk engine does not import any strategy code and cannot be bypassed by a
strategy: the backtester and the order manager both route every order intent
through ``RiskEngine.check``. It can reject every order, including orders the
strategy is most confident about. Risk-REDUCING orders (sells of owned shares) are
only blocked for structural reasons (duplicate, unowned, no market).

All limits are configuration HYPOTHESES (see docs/RISK_MODEL.md); none is claimed
optimal. A stop loss does not bound the loss of a trade: gaps fill through stops.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class RiskLimits:
    max_position_fraction: float = 0.60     # of equity, per symbol after the order
    max_gross_exposure: float = 1.00        # no leverage
    max_orders_per_day: int = 6
    max_new_positions_per_day: int = 2
    max_loss_per_trade_fraction: float = 0.05  # stop distance x position value <= 5% equity
    max_daily_loss_fraction: float = 0.05   # block new buys after a 5% down day
    max_drawdown_fraction: float = 0.25     # breach -> block new buys for drawdown_cooldown sessions
    drawdown_cooldown: int = 21             # then re-arm with the peak reset to current equity (0 = halt forever)
    max_participation: float = 0.02         # order qty <= 2% of 20-day avg volume
    max_staleness_days: int = 4             # calendar days between last bar and decision
    max_price_deviation: float = 0.10       # reference price vs last close sanity bound
    min_order_value: float = 0.0


@dataclass
class RiskState:
    peak_equity: float
    day_start_equity: float
    kill_switch: bool = False
    orders_today: int = 0
    new_positions_today: int = 0
    current_day: pd.Timestamp | None = None
    halt_sessions_left: int = 0
    halted_forever: bool = False
    drawdown_breaches: int = 0
    seen_order_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class OrderCheck:
    """Everything the risk engine needs about one order, supplied by the caller."""

    order_id: str
    symbol: str
    side: str
    qty: int
    ref_price: float
    last_close: float
    last_bar_date: pd.Timestamp | None
    avg_volume_20: float
    held_qty: int
    stop_frac: float | None = None


class RiskEngine:
    def __init__(self, limits: RiskLimits, initial_equity: float, audit_path: str | Path | None = None,
                 kill_switch_file: str | Path | None = None):
        self.limits = limits
        self.state = RiskState(peak_equity=initial_equity, day_start_equity=initial_equity)
        self.audit_path = Path(audit_path) if audit_path else None
        self.kill_switch_file = Path(kill_switch_file) if kill_switch_file else None
        self.log: list[dict] = []

    # --------------------------------------------------------------- lifecycle
    def start_day(self, day: pd.Timestamp, equity: float) -> None:
        st = self.state
        st.current_day = pd.Timestamp(day)
        st.day_start_equity = equity
        st.orders_today = 0
        st.new_positions_today = 0
        if st.halt_sessions_left > 0:
            st.halt_sessions_left -= 1
            if st.halt_sessions_left == 0:
                st.peak_equity = equity  # re-arm: drawdown measured from here
                self._audit({"event": "drawdown_halt_end", "day": str(st.current_day.date()), "equity": equity})
        st.peak_equity = max(st.peak_equity, equity)

    def update_equity(self, equity: float) -> None:
        self.state.peak_equity = max(self.state.peak_equity, equity)

    def set_kill_switch(self, on: bool, who: str, why: str) -> None:
        """Manual override. Always audited."""
        self.state.kill_switch = on
        self._audit({"event": "kill_switch", "on": on, "who": who, "why": why})

    def _kill_active(self) -> bool:
        return self.state.kill_switch or (self.kill_switch_file is not None and self.kill_switch_file.exists())

    # ------------------------------------------------------------------ checks
    def check(self, o: OrderCheck, *, equity: float, cash: float, gross_exposure: float,
              position_value: float, decision_time: pd.Timestamp) -> Decision:
        d = self._check(o, equity, cash, gross_exposure, position_value, decision_time)
        if d.approved:
            self.state.seen_order_ids.add(o.order_id)
            self.state.orders_today += 1
            if o.side == "BUY" and o.held_qty == 0:
                self.state.new_positions_today += 1
        self.log.append({"time": str(decision_time), "order_id": o.order_id, "symbol": o.symbol, "side": o.side,
                         "qty": o.qty, "approved": d.approved, "reason": d.reason})
        return d

    def _check(self, o: OrderCheck, equity, cash, gross_exposure, position_value, now) -> Decision:
        L, st = self.limits, self.state
        if o.order_id in st.seen_order_ids:
            return Decision(False, "duplicate_order_id")
        if not isinstance(o.qty, int) or o.qty <= 0:
            return Decision(False, "invalid_quantity")
        if o.side not in ("BUY", "SELL"):
            return Decision(False, "invalid_side")
        if not (o.ref_price > 0) or not (o.last_close > 0):
            return Decision(False, "no_valid_price")
        if abs(o.ref_price / o.last_close - 1) > L.max_price_deviation:
            return Decision(False, "price_sanity")
        if o.last_bar_date is None or (pd.Timestamp(now) - pd.Timestamp(o.last_bar_date)).days > L.max_staleness_days:
            return Decision(False, "stale_data")
        if st.orders_today >= L.max_orders_per_day:
            return Decision(False, "max_orders_per_day")
        if o.side == "SELL":
            if o.qty > o.held_qty:
                return Decision(False, "unowned_sale")
            return Decision(True, "ok")
        # ---- BUY: risk-increasing
        if self._kill_active():
            return Decision(False, "kill_switch")
        if equity <= 0:
            return Decision(False, "no_equity")
        if st.halted_forever or st.halt_sessions_left > 0:
            return Decision(False, "drawdown_halt")
        if st.peak_equity > 0 and 1 - equity / st.peak_equity > L.max_drawdown_fraction:
            st.drawdown_breaches += 1
            if L.drawdown_cooldown > 0:
                st.halt_sessions_left = L.drawdown_cooldown
            else:
                st.halted_forever = True
            self._audit({"event": "drawdown_halt_start", "equity": equity, "peak": st.peak_equity})
            return Decision(False, "max_drawdown_breached")
        if st.day_start_equity > 0 and 1 - equity / st.day_start_equity > L.max_daily_loss_fraction:
            return Decision(False, "max_daily_loss_breached")
        value = o.qty * o.ref_price
        if value < L.min_order_value:
            return Decision(False, "below_min_order_value")
        if value > cash:
            return Decision(False, "insufficient_cash")
        if (position_value + value) / equity > L.max_position_fraction + 1e-9:
            return Decision(False, "max_position_fraction")
        if (gross_exposure + value) / equity > L.max_gross_exposure + 1e-9:
            return Decision(False, "max_gross_exposure")
        if o.held_qty == 0 and st.new_positions_today >= L.max_new_positions_per_day:
            return Decision(False, "max_new_positions_per_day")
        if not (o.avg_volume_20 > 0) or o.qty > L.max_participation * o.avg_volume_20:
            return Decision(False, "liquidity")
        if o.stop_frac is not None and o.stop_frac * (position_value + value) > L.max_loss_per_trade_fraction * equity + 1e-9:
            return Decision(False, "max_loss_per_trade")
        return Decision(True, "ok")

    def _audit(self, rec: dict) -> None:
        rec = {"ts": pd.Timestamp.now(tz="UTC").isoformat(), **rec}
        self.log.append(rec)
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------ persistence
    _PERSIST = ("peak_equity", "day_start_equity", "kill_switch", "orders_today", "new_positions_today",
                "halt_sessions_left", "halted_forever", "drawdown_breaches")

    def state_dict(self) -> dict:
        d = {k: getattr(self.state, k) for k in self._PERSIST}
        d["current_day"] = str(self.state.current_day) if self.state.current_day is not None else None
        return d

    def load_state(self, d: dict) -> None:
        for k in self._PERSIST:
            setattr(self.state, k, d[k])
        self.state.current_day = pd.Timestamp(d["current_day"]) if d.get("current_day") else None

    def config(self) -> dict:
        return asdict(self.limits)
