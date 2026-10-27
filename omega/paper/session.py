"""Daily paper-trading session (Phase 5).

Run once after each market close (e.g. by Windows Task Scheduler):

    python -m omega paper --data DIR --state runs/paper_m1 --strategy M1_xs_momentum

For every session not yet processed, in order:
 1. corporate actions are applied to the paper account;
 2. orders submitted after the previous close are matched at this session's open
    (same slippage and charges as the backtester); unmatched day orders expire;
 3. the OrderManager reconciles local order state with the broker;
 4. protective stops are checked against the session low;
 5. positions without bars for ``delist_after`` sessions are written off (and alerted);
 6. the account is marked at the close;
 7. the strategy decides via the SAME ``plan_orders`` code as the backtester (allocator
    + independent risk engine) and approved orders are submitted for the next open.

State lives in ``state_dir`` (SQLite for session state and orders, JSONL risk audit), so
the job is restartable: re-running for an already processed date is a no-op, and a
crash is recovered by reconciliation (order keys are deterministic per decision).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestConfig, market_arrays, plan_orders
from ..costs.model import CostModel
from ..data.market import MarketData
from ..execution.broker import PaperBroker
from ..execution.manager import OrderManager
from ..execution.orders import S
from ..risk.engine import RiskEngine
from ..strategies.base import BookView, Holding, Strategy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily (date TEXT PRIMARY KEY, body TEXT NOT NULL);
"""


class PaperSession:
    def __init__(self, state_dir: str | Path, strategy: Strategy, cfg: BacktestConfig = BacktestConfig()):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.strategy, self.cfg = strategy, cfg
        self.db = sqlite3.connect(str(self.dir / "session.sqlite"))
        try:
            _check_integrity(self.db)
        except sqlite3.DatabaseError:
            self.db.close()  # do not leave the (corrupted) file locked
            raise
        self.db.executescript(_SCHEMA)
        self.costs = CostModel(cfg.broker, cfg.cost_schedule)
        # The simulated broker persists itself on every mutation (it plays an external system).
        self.broker = PaperBroker.open(self.dir / "paper_broker.json", cfg.initial_cash, fee_fn=self._fee_fn)
        self.risk = RiskEngine(cfg.risk, cfg.initial_cash, audit_path=self.dir / "risk_audit.jsonl",
                               kill_switch_file=self.dir / "KILL_SWITCH")
        if (rs := self._get("risk")) is not None:
            self.risk.load_state(rs)
        self.manager = OrderManager(self.broker, self.dir / "orders.sqlite")
        self.meta: dict = self._get("meta") or {}
        self.pending: dict = self._get("pending") or {}
        self._fee_date = None

    def close(self) -> None:
        """Release the database files (on Windows an open connection keeps them locked)."""
        self.manager.close()
        self.db.close()

    def __enter__(self) -> "PaperSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ storage
    def _get(self, key):
        with closing(self.db.execute("SELECT value FROM kv WHERE key=?", (key,))) as cur:
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def _persist(self, day_record: dict) -> None:
        """All session state for one day is committed in ONE transaction."""
        kv = {"risk": self.risk.state_dict(), "meta": self.meta,
              "pending": self.pending, "last_session": day_record["date"], "prev_equity": day_record["equity"],
              "strategy": self.strategy.describe()}
        with self.db:
            for k, v in kv.items():
                self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v, default=str)))
            self.db.execute("INSERT OR REPLACE INTO daily VALUES (?,?)",
                            (day_record["date"], json.dumps(day_record, default=str)))

    def _fee_fn(self, side, value, first_sell=True):
        return self.costs.charges(side, value, self._fee_date, first_sell_of_scrip_today=first_sell).total

    def history(self) -> pd.DataFrame:
        with closing(self.db.execute("SELECT body FROM daily ORDER BY date")) as cur:
            rows = [json.loads(r[0]) for r in cur.fetchall()]
        df = pd.DataFrame(rows)
        if len(df):
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def backup(self, dest_dir: str | Path) -> Path:
        """Consistent online copy of both databases (sqlite backup API) and the paper broker file."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for name, con in (("session.sqlite", self.db), ("orders.sqlite", self.manager.db)):
            with closing(sqlite3.connect(str(dest / name))) as out:
                con.backup(out)
        shutil.copy2(self.broker.path, dest / self.broker.path.name)
        return dest

    # ------------------------------------------------------------------ running
    def run_until(self, data: MarketData, date: pd.Timestamp | None = None) -> list[dict]:
        """Process every unprocessed session up to ``date`` (default: last bar in ``data``)."""
        end = len(data) - 1 if date is None else int(data.dates.searchsorted(pd.Timestamp(date), side="right")) - 1
        if end < 0:
            return []
        last = self._get("last_session")
        start = 0 if last is None else int(data.dates.searchsorted(pd.Timestamp(last), side="right"))
        if start > end:
            return []
        view = data.truncate(end + 1)  # nothing after `date` is visible
        self.strategy.prepare(view)
        m = market_arrays(view)
        actions_at: dict[int, list] = {}
        for a in view.actions.itertuples(index=False):
            k = int(view.dates.searchsorted(pd.Timestamp(a.ex_date)))
            if k < len(view) and a.symbol in m.sidx:
                actions_at.setdefault(k, []).append(a)
        return [self._step(k, m, actions_at.get(k, [])) for k in range(start, end + 1)]

    def _step(self, k: int, m, actions) -> dict:
        d = m.dates[k]
        self._fee_date = d
        cfg, slip, b = self.cfg, self.cfg.slippage, self.broker
        events: list[dict] = []
        prev_eq = self._get("prev_equity")
        self.risk.start_day(d, cfg.initial_cash if prev_eq is None else float(prev_eq))
        b.start_day(str(d.date()))

        # 1. corporate actions
        for a in actions:
            j = m.sidx[a.symbol]
            if a.kind == "split":
                ref = m.O[k, j] if m.has_bar[k, j] else m.C_ff[k, j] / a.value
                before = b.positions().get(a.symbol, 0)
                first = f"split:{a.symbol}:{d.date()}" not in b.applied_events
                b.apply_split(a.symbol, float(a.value), float(ref), event_id=f"split:{a.symbol}:{d.date()}")
                if first:
                    self.manager.record_adjustment(a.symbol, b.positions().get(a.symbol, 0) - before,
                                                   f"split x{a.value} {d.date()}")
                if a.symbol in self.meta and self.meta[a.symbol].get("stop") is not None:
                    self.meta[a.symbol]["stop"] /= float(a.value)
                for p in self.pending.values():
                    if p["symbol"] == a.symbol:
                        p["ref_price"] /= float(a.value)
                events.append({"kind": "split", "symbol": a.symbol, "value": float(a.value)})
            elif a.kind == "dividend":
                amt = b.apply_dividend(a.symbol, float(a.value), event_id=f"div:{a.symbol}:{d.date()}")
                if amt:
                    events.append({"kind": "dividend", "symbol": a.symbol, "amount": amt})

        # 2. open: match resting orders (sells first), expire the rest
        opens = {s: float(m.O[k, j]) for s, j in m.sidx.items() if m.has_bar[k, j]}
        price_fn = lambda s, side, q, ref: slip.price(ref, side, q, m.V[k, m.sidx[s]])  # noqa: E731
        today = str(d.date())
        can_fill = lambda s, side: m.can_fill(k, m.sidx[s], side)  # noqa: E731
        b.match(opens, price_fn, session=today, can_fill=can_fill)
        b.expire_day(session=today)
        rec = self.manager.reconcile()
        fills_today = []
        for cid, p in list(self.pending.items()):
            o = self.manager.get(cid)
            if o is not None and o.filled_qty > 0:
                j = m.sidx[p["symbol"]]
                model = slip.price(m.O[k, j], p["side"], o.filled_qty, m.V[k, j])
                fills_today.append({"symbol": p["symbol"], "side": p["side"], "qty": o.filled_qty,
                                    "price": o.avg_price, "model_price": model,
                                    "divergence_bps": 1e4 * (o.avg_price / model - 1)})
                if p["side"] == "BUY" and p["symbol"] not in self.meta:
                    self.meta[p["symbol"]] = {
                        "entry_index": k, "entry_date": str(d.date()), "missing": 0,
                        "cost": o.filled_qty * o.avg_price,
                        "stop": p["ref_price"] * (1 - p["stop_frac"]) if p.get("stop_frac") else None}
            elif o is not None and o.status in (S.REJECTED, S.EXPIRED, S.CANCELLED):
                events.append({"kind": "order_" + o.status.value.lower(), "symbol": p["symbol"], "cid": cid})
            if o is None or o.terminal:
                del self.pending[cid]
        self._sync_meta()

        # 3. protective stops
        for s in list(b.positions()):
            j = m.sidx[s]
            stop = self.meta.get(s, {}).get("stop")
            if stop is None or not m.has_bar[k, j] or m.L[k, j] > stop:
                continue
            if not m.can_fill(k, j, "SELL"):
                events.append({"kind": "stop_blocked_circuit", "symbol": s})
                continue
            gap = m.O[k, j] <= stop
            ref = float(m.O[k, j] if gap else stop)
            r = self.manager.submit(self.strategy.name, d, s, "SELL", b.positions()[s], attempt=99)
            b.match({s: ref}, price_fn, symbols={s})
            self.manager.reconcile()
            events.append({"kind": "stop_gap" if gap else "stop", "symbol": s, "cid": r.order.client_order_id})
        self._sync_meta()

        # 4. delisting / long suspension
        for s in list(b.positions()):
            j = m.sidx[s]
            self.meta[s]["missing"] = 0 if m.has_bar[k, j] else self.meta[s].get("missing", 0) + 1
            if s in b.positions() and self.meta[s]["missing"] >= cfg.delist_after:
                q = b.positions()[s]
                b.write_off(s, q * m.C_ff[k, j] * cfg.delist_recovery, event_id=f"writeoff:{s}:{d.date()}")
                self.manager.record_adjustment(s, -q, f"write-off {d.date()}")
                events.append({"kind": "write_off_ALERT", "symbol": s,
                               "detail": "no bars - verify delisting with the depository/broker"})
        self._sync_meta()

        # 5. mark at the close
        pos = b.positions()
        mv = float(sum(q * m.C_ff[k, m.sidx[s]] for s, q in pos.items()))
        equity = b.cash() + mv
        self.risk.update_equity(equity)

        # 6. decide for the next session (identical code path to the backtester)
        book = BookView(equity=equity, cash=b.cash(), holdings={
            s: Holding(q, self.meta[s]["entry_index"], self.meta[s].get("cost", 0.0) / q) for s, q in pos.items()})
        approved, rejected = plan_orders(self.strategy, k, book, mv, m, self.costs, cfg, self.risk)
        submitted = []
        for _, it in approved:
            r = self.manager.submit(self.strategy.name, d, it.symbol, it.side, it.qty, after_close=True)
            if r.order.status in (S.REJECTED,):
                events.append({"kind": "submit_rejected", "symbol": it.symbol, "note": r.note})
                continue
            self.pending[r.order.client_order_id] = {"symbol": it.symbol, "side": it.side, "qty": it.qty,
                                                     "ref_price": it.ref_price, "stop_frac": it.stop_frac,
                                                     "reason": it.reason}
            submitted.append({"symbol": it.symbol, "side": it.side, "qty": it.qty, "reason": it.reason})
        record = {"date": str(d.date()), "equity": equity, "cash": b.cash(), "positions": pos,
                  "fees_paid_cum": b.fees_paid, "fills": fills_today, "submitted": submitted,
                  "risk_rejected": [{"symbol": it.symbol, "reason": why} for _, it, why in rejected],
                  "reconciliation_breaks": rec.get("breaks", {}), "halted": self.manager.halted, "events": events}
        self._persist(record)
        return record

    def _sync_meta(self) -> None:
        held = self.broker.positions()
        for s in list(self.meta):
            if s not in held:
                del self.meta[s]


def _check_integrity(con: sqlite3.Connection) -> None:
    with closing(con.execute("PRAGMA integrity_check")) as cur:
        res = cur.fetchone()[0]
    if res != "ok":
        raise sqlite3.DatabaseError(f"state database failed integrity check: {res}")

