"""Explicit order state machine.

CREATED -> VALIDATED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
Terminal alternatives: REJECTED, CANCELLED, EXPIRED.
UNKNOWN is entered when the broker response is ambiguous (timeout, dropped
connection). An UNKNOWN order is never resubmitted; it is resolved only by
reconciliation against the broker's own record.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


S = OrderStatus
TERMINAL = {S.FILLED, S.REJECTED, S.CANCELLED, S.EXPIRED}
_AFTER_SUBMIT = {S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED, S.CANCELLED, S.EXPIRED, S.UNKNOWN}
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    S.CREATED: {S.VALIDATED, S.REJECTED},
    S.VALIDATED: {S.SUBMITTED, S.REJECTED, S.CANCELLED},
    S.SUBMITTED: set(_AFTER_SUBMIT),
    S.UNKNOWN: _AFTER_SUBMIT - {S.UNKNOWN},
    S.ACKNOWLEDGED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.EXPIRED, S.REJECTED},
    S.PARTIALLY_FILLED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.EXPIRED},
    S.FILLED: set(),
    S.REJECTED: set(),
    S.CANCELLED: set(),
    S.EXPIRED: set(),
}


class IllegalTransition(RuntimeError):
    pass


def idempotency_key(strategy: str, session: pd.Timestamp | str, symbol: str, side: str, qty: int, attempt: int = 0) -> str:
    """Deterministic client order id: the same decision always maps to the same key,
    so a restarted process cannot create a second order for one decision."""
    raw = f"{strategy}|{pd.Timestamp(session).date()}|{symbol}|{side}|{qty}|{attempt}"
    return "OMG" + hashlib.sha256(raw.encode()).hexdigest()[:17].upper()


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: str
    qty: int
    strategy: str
    created: str
    status: OrderStatus = S.CREATED
    filled_qty: int = 0
    avg_price: float = 0.0
    broker_order_id: str | None = None
    reason: str = ""
    history: list[tuple[str, str, str]] = field(default_factory=list)

    def transition(self, new: OrderStatus, note: str = "") -> None:
        if new not in TRANSITIONS[self.status]:
            raise IllegalTransition(f"{self.client_order_id}: {self.status.value} -> {new.value} not allowed")
        self.history.append((pd.Timestamp.now(tz="UTC").isoformat(), new.value, note))
        self.status = new

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty
