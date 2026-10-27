"""Broker adapter interface and a simulated paper broker with failure injection.

There is deliberately NO live broker adapter in this code base. See
omega/execution/live_guard.py and docs/SECURITY.md.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np


class BrokerError(RuntimeError):
    """Definitive rejection: the order was NOT accepted."""


class BrokerTimeout(RuntimeError):
    """Ambiguous: the order may or may not have been accepted."""


@dataclass(frozen=True)
class BrokerFill:
    fill_id: str
    qty: int
    price: float


@dataclass
class BrokerOrder:
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    qty: int
    status: str  # ACKNOWLEDGED | PARTIALLY_FILLED | FILLED | REJECTED | CANCELLED | EXPIRED
    fills: list[BrokerFill] = field(default_factory=list)
    placed: str | None = None  # session (ISO date) after whose close it was placed; None = immediately eligible

    @property
    def filled_qty(self) -> int:
        return sum(f.qty for f in self.fills)


class BrokerAdapter(Protocol):
    is_live: bool

    def submit(self, client_order_id: str, symbol: str, side: str, qty: int, placed: str | None = None) -> BrokerOrder: ...
    def get_order(self, client_order_id: str) -> BrokerOrder | None: ...
    def cancel(self, client_order_id: str) -> None: ...
    def positions(self) -> dict[str, int]: ...
    def cash(self) -> float: ...


@dataclass
class FailureConfig:
    timeout_before_accept: float = 0.0   # request lost: broker never saw it
    timeout_after_accept: float = 0.0    # broker accepted, response lost
    reject: float = 0.0
    partial_fill: float = 0.0            # fill only part on a given match
    seed: int = 0


class PaperBroker:
    """In-memory simulated broker. Idempotent on client_order_id like real brokers that
    support client tags; matches resting orders when ``match`` is called with prices."""

    is_live = False

    def __init__(self, cash: float, failures: FailureConfig | None = None, fee_fn=None, path: str | Path | None = None):
        """fee_fn(side, value, first_sell_of_scrip_today) -> INR charges for one fill.

        path: if given, the broker persists itself after every mutation (atomic replace), so its
        state survives a crash of OUR process - exactly like a real, external broker."""
        self.path = Path(path) if path else None
        self.applied_events: set[str] = set()
        self._cash = float(cash)
        self._pos: dict[str, int] = {}
        self.orders: dict[str, BrokerOrder] = {}
        self.f = failures or FailureConfig()
        self._rng = np.random.default_rng(self.f.seed)
        self._seq = 0
        self.fee_fn = fee_fn or (lambda side, value, first_sell=True: 0.0)
        self.submit_calls = 0
        self._sold_today: set[str] = set()
        self.fees_paid = 0.0

    # ------------------------------------------------------------ persistence
    def to_state(self) -> dict:
        return {"cash": self._cash, "positions": self._pos, "seq": self._seq, "fees_paid": self.fees_paid,
                "applied_events": sorted(self.applied_events), "sold_today": sorted(self._sold_today),
                "orders": {k: {**{f: getattr(o, f) for f in ("client_order_id", "broker_order_id", "symbol", "side",
                                                            "qty", "status", "placed")},
                               "fills": [[x.fill_id, x.qty, x.price] for x in o.fills]}
                           for k, o in self.orders.items()}}

    @classmethod
    def from_state(cls, st: dict, failures: FailureConfig | None = None, fee_fn=None,
                   path: str | Path | None = None) -> "PaperBroker":
        b = cls(st["cash"], failures, fee_fn, path)
        b._pos = {k: int(v) for k, v in st["positions"].items()}
        b.applied_events = set(st.get("applied_events", []))
        b._sold_today = set(st.get("sold_today", []))
        b._seq = st["seq"]
        b.fees_paid = st.get("fees_paid", 0.0)
        for k, o in st["orders"].items():
            fills = [BrokerFill(fid, int(q), float(p)) for fid, q, p in o.pop("fills")]
            b.orders[k] = BrokerOrder(**o, fills=fills)
        return b

    @classmethod
    def open(cls, path: str | Path, cash: float, failures: FailureConfig | None = None, fee_fn=None) -> "PaperBroker":
        p = Path(path)
        if p.exists():
            return cls.from_state(json.loads(p.read_text(encoding="utf-8")), failures, fee_fn, p)
        b = cls(cash, failures, fee_fn, p)
        b._save()
        return b

    def _save(self) -> None:
        if self.path is None:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_state()), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on Windows and POSIX

    def _once(self, event_id: str | None) -> bool:
        """True if the event should be applied (first time). Makes re-runs idempotent."""
        if event_id is None:
            return True
        if event_id in self.applied_events:
            return False
        self.applied_events.add(event_id)
        return True

    # ------------------------------------------------------- session events
    def start_day(self, day_id: str | None = None) -> None:
        if self._once(None if day_id is None else f"day:{day_id}"):
            self._sold_today = set()
            self._save()

    def apply_split(self, symbol: str, multiplier: float, ref_price: float, event_id: str | None = None) -> None:
        """Mirror of the depository: shares x multiplier, fraction paid as cash in lieu.
        Resting orders for the symbol are adjusted like the exchange would."""
        if not self._once(event_id):
            return
        q = self._pos.get(symbol, 0)
        if q:
            new_f = q * multiplier
            new_q = int(new_f + 1e-9)
            self._cash += round((new_f - new_q) * ref_price, 2)
            if new_q:
                self._pos[symbol] = new_q
            else:
                del self._pos[symbol]
        for bo in self.orders.values():
            if bo.symbol == symbol and bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                bo.qty = bo.filled_qty + int((bo.qty - bo.filled_qty) * multiplier)
        self._save()

    def write_off(self, symbol: str, recovery_value: float, event_id: str | None = None) -> None:
        """Simulates a position that can no longer be traded (delisting) settling for cash."""
        if self._once(event_id) and self._pos.pop(symbol, None) is not None:
            self._cash += round(max(recovery_value, 0.0), 2)
        self._save()

    def apply_dividend(self, symbol: str, per_share: float, event_id: str | None = None) -> float:
        if not self._once(event_id):
            return 0.0
        amt = round(self._pos.get(symbol, 0) * per_share, 2)
        self._cash += amt
        self._save()
        return amt

    def submit(self, client_order_id: str, symbol: str, side: str, qty: int, placed: str | None = None) -> BrokerOrder:
        """placed: session date after whose close the order was entered (an after-market order);
        it becomes eligible only in a LATER session, like a broker's AMO."""
        self.submit_calls += 1
        if client_order_id in self.orders:  # idempotent: same tag -> same order
            return self.orders[client_order_id]
        if self._rng.random() < self.f.timeout_before_accept:
            raise BrokerTimeout("request timed out before reaching broker")
        if self._rng.random() < self.f.reject:
            raise BrokerError("rejected by broker (simulated)")
        self._seq += 1
        bo = BrokerOrder(client_order_id, f"PB{self._seq:06d}", symbol, side, qty, "ACKNOWLEDGED", placed=placed)
        self.orders[client_order_id] = bo
        self._save()
        if self._rng.random() < self.f.timeout_after_accept:
            raise BrokerTimeout("response lost after broker accepted the order")
        return bo

    @staticmethod
    def _eligible(bo: BrokerOrder, session: str | None) -> bool:
        return session is None or bo.placed is None or bo.placed < session

    def match(self, prices: dict[str, float], price_fn=None, symbols: set[str] | None = None,
              session: str | None = None, can_fill=None) -> list[str]:
        """Fill resting orders (sells first, like a broker freeing cash).

        prices: reference price per symbol (e.g. the session open). price_fn(symbol, side, qty, ref)
        may return the actual execution price (slippage). Cash and holdings are checked like a
        broker RMS: an unaffordable buy or an oversized sell is REJECTED, not shrunk.
        Returns client ids that received fills.
        """
        filled = []
        live = [bo for bo in self.orders.values() if bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED")
                and (symbols is None or bo.symbol in symbols) and self._eligible(bo, session)]
        for bo in sorted(live, key=lambda o: (o.side != "SELL", o.broker_order_id)):
            ref = prices.get(bo.symbol)
            if ref is None or not math.isfinite(ref):
                continue
            if can_fill is not None and not can_fill(bo.symbol, bo.side):
                continue  # no counterparty (e.g. circuit-locked); a day order then expires
            rem = bo.qty - bo.filled_qty
            q = rem
            if self._rng.random() < self.f.partial_fill and rem > 1:
                q = max(1, rem // 2)
            px = price_fn(bo.symbol, bo.side, q, ref) if price_fn else ref
            value = q * px
            if bo.side == "BUY":
                fee = self.fee_fn("BUY", value, False)
                if value + fee > self._cash + 1e-9:
                    bo.status = "REJECTED"
                    continue
                self._cash -= value + fee
                self._pos[bo.symbol] = self._pos.get(bo.symbol, 0) + q
            else:
                if self._pos.get(bo.symbol, 0) < q:
                    bo.status = "REJECTED"
                    continue
                fee = self.fee_fn("SELL", value, bo.symbol not in self._sold_today)
                self._sold_today.add(bo.symbol)
                self._cash += value - fee
                self._pos[bo.symbol] -= q
                if self._pos[bo.symbol] == 0:
                    del self._pos[bo.symbol]
            self.fees_paid += fee
            bo.fills.append(BrokerFill(f"{bo.broker_order_id}-F{len(bo.fills) + 1}", q, px))
            bo.status = "FILLED" if bo.filled_qty == bo.qty else "PARTIALLY_FILLED"
            filled.append(bo.client_order_id)
        self._save()
        return filled

    def expire_day(self, session: str | None = None) -> None:
        for bo in self.orders.values():
            if bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED") and self._eligible(bo, session):
                bo.status = "EXPIRED"
        self._save()

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return self.orders.get(client_order_id)

    def cancel(self, client_order_id: str) -> None:
        bo = self.orders.get(client_order_id)
        if bo and bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
            bo.status = "CANCELLED"
            self._save()

    def positions(self) -> dict[str, int]:
        return dict(self._pos)

    def cash(self) -> float:
        return self._cash
