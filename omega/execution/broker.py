"""Broker adapter interface and a simulated paper broker with failure injection.

There is deliberately NO live broker adapter in this code base. See
omega/execution/live_guard.py and docs/SECURITY.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
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

    @property
    def filled_qty(self) -> int:
        return sum(f.qty for f in self.fills)


class BrokerAdapter(Protocol):
    is_live: bool

    def submit(self, client_order_id: str, symbol: str, side: str, qty: int) -> BrokerOrder: ...
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

    def __init__(self, cash: float, failures: FailureConfig | None = None, fee_fn=None):
        self._cash = float(cash)
        self._pos: dict[str, int] = {}
        self.orders: dict[str, BrokerOrder] = {}
        self.f = failures or FailureConfig()
        self._rng = np.random.default_rng(self.f.seed)
        self._seq = 0
        self.fee_fn = fee_fn or (lambda side, value: 0.0)
        self.submit_calls = 0

    def submit(self, client_order_id: str, symbol: str, side: str, qty: int) -> BrokerOrder:
        self.submit_calls += 1
        if client_order_id in self.orders:  # idempotent: same tag -> same order
            return self.orders[client_order_id]
        if self._rng.random() < self.f.timeout_before_accept:
            raise BrokerTimeout("request timed out before reaching broker")
        if self._rng.random() < self.f.reject:
            raise BrokerError("rejected by broker (simulated)")
        self._seq += 1
        bo = BrokerOrder(client_order_id, f"PB{self._seq:06d}", symbol, side, qty, "ACKNOWLEDGED")
        self.orders[client_order_id] = bo
        if self._rng.random() < self.f.timeout_after_accept:
            raise BrokerTimeout("response lost after broker accepted the order")
        return bo

    def match(self, prices: dict[str, float]) -> None:
        """Fill resting orders at the given prices (cash/position checked like an RMS)."""
        for bo in self.orders.values():
            if bo.status not in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                continue
            px = prices.get(bo.symbol)
            if px is None or not math.isfinite(px):
                continue
            rem = bo.qty - bo.filled_qty
            q = rem
            if self._rng.random() < self.f.partial_fill and rem > 1:
                q = max(1, rem // 2)
            value = q * px
            fee = self.fee_fn(bo.side, value)
            if bo.side == "BUY":
                if value + fee > self._cash:
                    bo.status = "REJECTED"
                    continue
                self._cash -= value + fee
                self._pos[bo.symbol] = self._pos.get(bo.symbol, 0) + q
            else:
                if self._pos.get(bo.symbol, 0) < q:
                    bo.status = "REJECTED"
                    continue
                self._cash += value - fee
                self._pos[bo.symbol] -= q
                if self._pos[bo.symbol] == 0:
                    del self._pos[bo.symbol]
            bo.fills.append(BrokerFill(f"{bo.broker_order_id}-F{len(bo.fills) + 1}", q, px))
            bo.status = "FILLED" if bo.filled_qty == bo.qty else "PARTIALLY_FILLED"

    def expire_day(self) -> None:
        for bo in self.orders.values():
            if bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                bo.status = "EXPIRED"

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return self.orders.get(client_order_id)

    def cancel(self, client_order_id: str) -> None:
        bo = self.orders.get(client_order_id)
        if bo and bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
            bo.status = "CANCELLED"

    def positions(self) -> dict[str, int]:
        return dict(self._pos)

    def cash(self) -> float:
        return self._cash
