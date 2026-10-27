"""Order manager: risk-gated, idempotent, write-ahead persisted, reconciling.

Rules:
1. Every order gets a deterministic idempotency key; an existing key is never
   submitted twice.
2. The order is persisted as SUBMITTED *before* the broker call (write-ahead), so a
   crash mid-submission leaves evidence that reconciliation will resolve.
3. A timeout moves the order to UNKNOWN. UNKNOWN orders are never resubmitted;
   ``reconcile`` asks the broker what happened.
4. Fills are applied exactly once (deduplicated by broker fill id).
5. If broker positions disagree with local positions, the manager halts new
   submissions until a human resolves the break.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .broker import BrokerAdapter, BrokerError, BrokerTimeout
from .orders import TRANSITIONS, S, Order, OrderStatus, idempotency_key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
  fill_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
  qty INTEGER NOT NULL, price REAL NOT NULL, applied TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (ts TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adjustments (
  ts TEXT NOT NULL, symbol TEXT NOT NULL, qty_delta INTEGER NOT NULL, reason TEXT NOT NULL);
"""


@dataclass(frozen=True)
class SubmitResult:
    order: Order
    submitted_now: bool
    note: str


class OrderManager:
    def __init__(self, broker: BrokerAdapter, db_path: str | Path = ":memory:", risk_check=None,
                 not_found_limit: int = 2):
        if getattr(broker, "is_live", False):
            raise PermissionError("OrderManager refuses live brokers in this version (see live_guard.py)")
        self.broker = broker
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(_SCHEMA)
        self.risk_check = risk_check  # callable(Order) -> (approved: bool, reason: str)
        self.halted = False
        self.not_found_limit = not_found_limit
        self._not_found: dict[str, int] = {}

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- persistence
    def _save(self, o: Order) -> None:
        body = json.dumps({**o.__dict__, "status": o.status.value})
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?)",
                            (o.client_order_id, body, o.status.value, pd.Timestamp.now(tz="UTC").isoformat()))

    def _event(self, kind: str, **body) -> None:
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?)",
                            (pd.Timestamp.now(tz="UTC").isoformat(), kind, json.dumps(body, default=str)))

    def get(self, cid: str) -> Order | None:
        with closing(self.db.execute("SELECT body FROM orders WHERE client_order_id=?", (cid,))) as cur:
            row = cur.fetchone()
        if not row:
            return None
        d = json.loads(row[0])
        d["status"] = OrderStatus(d["status"])
        d["history"] = [tuple(h) for h in d["history"]]
        return Order(**d)

    def orders(self) -> list[Order]:
        with closing(self.db.execute("SELECT client_order_id FROM orders")) as cur:
            ids = [r[0] for r in cur.fetchall()]
        return [self.get(i) for i in ids]  # type: ignore[misc]

    def events(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM events", self.db)

    def local_positions(self) -> dict[str, int]:
        """Positions implied by our own fills plus recorded non-trade adjustments."""
        pos: dict[str, int] = {}
        with closing(self.db.execute("SELECT symbol, side, SUM(qty) FROM fills GROUP BY symbol, side")) as cur:
            for sym, side, q in cur.fetchall():
                pos[sym] = pos.get(sym, 0) + (q if side == "BUY" else -q)
        with closing(self.db.execute("SELECT symbol, SUM(qty_delta) FROM adjustments GROUP BY symbol")) as cur:
            for sym, q in cur.fetchall():
                pos[sym] = pos.get(sym, 0) + q
        return {k: v for k, v in pos.items() if v != 0}

    def record_adjustment(self, symbol: str, qty_delta: int, reason: str) -> None:
        """Non-trade position change (split/bonus, delisting write-off), audited. Without it,
        reconciliation would correctly flag the change as an unexplained break."""
        if qty_delta == 0:
            return
        with self.db:
            self.db.execute("INSERT INTO adjustments VALUES (?,?,?,?)",
                            (pd.Timestamp.now(tz="UTC").isoformat(), symbol, int(qty_delta), reason))
        self._event("position_adjustment", symbol=symbol, qty_delta=int(qty_delta), reason=reason)

    # -------------------------------------------------------------------- submit
    def submit(self, strategy: str, session, symbol: str, side: str, qty: int, attempt: int = 0,
               after_close: bool = False) -> SubmitResult:
        """after_close=True marks an after-market order: eligible from the session after ``session``."""
        cid = idempotency_key(strategy, session, symbol, side, qty, attempt)
        existing = self.get(cid)
        if existing is not None:
            self._event("duplicate_submit_blocked", cid=cid, status=existing.status.value)
            return SubmitResult(existing, False, "duplicate: existing order returned, not resubmitted")
        o = Order(cid, symbol, side, qty, strategy, pd.Timestamp.now(tz="UTC").isoformat())
        if self.halted:
            o.transition(S.REJECTED, "manager halted (reconciliation break or kill switch)")
            self._save(o)
            return SubmitResult(o, False, "halted")
        ok, why = self.risk_check(o) if self.risk_check else (True, "no risk check configured")
        if not ok:
            o.transition(S.REJECTED, f"risk: {why}")
            self._save(o)
            return SubmitResult(o, False, why)
        o.transition(S.VALIDATED, why)
        o.transition(S.SUBMITTED, "write-ahead before broker call")
        self._save(o)
        try:
            placed = str(pd.Timestamp(session).date()) if after_close else None
            bo = self.broker.submit(cid, symbol, side, qty, placed=placed)
        except BrokerTimeout as e:
            o.transition(S.UNKNOWN, str(e))
            self._save(o)
            self._event("ambiguous_submit", cid=cid, error=str(e))
            return SubmitResult(o, True, "UNKNOWN - will reconcile, never resubmit")
        except BrokerError as e:
            o.transition(S.REJECTED, str(e))
            self._save(o)
            return SubmitResult(o, True, "broker rejected")
        o.broker_order_id = bo.broker_order_id
        o.transition(S.ACKNOWLEDGED, "broker ack")
        self._save(o)
        self._sync(o, bo)
        return SubmitResult(o, True, "acknowledged")

    # ----------------------------------------------------------------- reconcile
    def _apply_fills(self, o: Order, bo) -> None:
        for f in bo.fills:
            with self.db:
                cur = self.db.execute("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?)",
                                      (f.fill_id, o.client_order_id, o.symbol, o.side, f.qty, f.price,
                                       pd.Timestamp.now(tz="UTC").isoformat()))
                if cur.rowcount == 0:
                    continue  # duplicate fill report: already applied
            tot = o.avg_price * o.filled_qty + f.price * f.qty
            o.filled_qty += f.qty
            o.avg_price = tot / o.filled_qty

    def _sync(self, o: Order, bo) -> None:
        self._apply_fills(o, bo)
        target = OrderStatus(bo.status)
        if target != o.status:
            if target not in TRANSITIONS[o.status]:
                self._event("illegal_broker_state", cid=o.client_order_id, local=o.status.value, broker=target.value)
                self.halted = True
            else:
                o.transition(target, "broker state")
        self._save(o)

    def reconcile(self) -> dict:
        """Resolve every non-terminal order against the broker, then compare positions."""
        report = {"resolved": [], "still_open": [], "breaks": {}}
        for o in self.orders():
            if o.terminal:
                continue
            bo = self.broker.get_order(o.client_order_id)
            if bo is None:
                if o.status in (S.UNKNOWN, S.SUBMITTED):
                    n = self._not_found[o.client_order_id] = self._not_found.get(o.client_order_id, 0) + 1
                    if n >= self.not_found_limit:
                        o.transition(S.REJECTED, f"not found at broker after {n} reconciliations")
                        self._save(o)
                        report["resolved"].append(o.client_order_id)
                        continue
                else:
                    # The broker acknowledged this order earlier and now has no record of it.
                    # That is never normal: stop trading until a human looks.
                    self.halted = True
                    self._event("acknowledged_order_missing", cid=o.client_order_id, status=o.status.value)
                report["still_open"].append(o.client_order_id)
                continue
            if o.status == S.SUBMITTED:
                o.transition(S.UNKNOWN, "found SUBMITTED on restart")
            if o.status == S.UNKNOWN and bo.status in ("ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED"):
                o.broker_order_id = bo.broker_order_id
            self._sync(o, bo)
            (report["resolved"] if o.terminal else report["still_open"]).append(o.client_order_id)
        local, remote = self.local_positions(), self.broker.positions()
        for s in set(local) | set(remote):
            if local.get(s, 0) != remote.get(s, 0):
                report["breaks"][s] = {"local": local.get(s, 0), "broker": remote.get(s, 0)}
        if report["breaks"]:
            self.halted = True
            self._event("reconciliation_break", **report["breaks"])
        self._event("reconcile", resolved=len(report["resolved"]), open=len(report["still_open"]),
                    breaks=len(report["breaks"]))
        return report
