"""Cash-only position ledger with enforced accounting invariants.

Every cash movement is a journal entry; ``check_invariants`` recomputes cash and
positions from the journal and compares them with the running state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

EPS = 1e-6


class LedgerError(RuntimeError):
    pass


class InsufficientCash(LedgerError):
    pass


class UnownedSale(LedgerError):
    pass


@dataclass
class Position:
    qty: int
    cost: float  # total INR paid for current qty, including buy-side charges
    entry_date: pd.Timestamp
    entry_index: int
    stop_price: float | None = None  # raw price; None = no stop

    @property
    def avg_cost(self) -> float:
        return self.cost / self.qty


@dataclass(frozen=True)
class Fill:
    date: pd.Timestamp
    symbol: str
    side: str
    qty: int
    price: float
    fees: float
    reason: str
    order_id: str


@dataclass(frozen=True)
class Journal:
    date: pd.Timestamp
    kind: str  # BUY | SELL | DIVIDEND | CASH_IN_LIEU | SPLIT | WRITE_OFF | DEPOSIT
    symbol: str | None
    qty_delta: int
    cash_delta: float
    ref: str = ""


@dataclass
class Ledger:
    initial_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    journal: list[Journal] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    dividends: float = 0.0
    closed_trades: list[dict] = field(default_factory=list)
    _applied_orders: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.initial_cash < 0:
            raise ValueError("initial cash must be >= 0")
        self.cash = float(self.initial_cash)
        self.journal.append(Journal(pd.Timestamp.min, "DEPOSIT", None, 0, float(self.initial_cash)))

    # ------------------------------------------------------------------ trades
    def buy(self, date, symbol: str, qty: int, price: float, fees: float, order_id: str, *, index: int = -1, reason: str = "") -> Fill:
        self._check_trade(qty, price, fees, order_id)
        outlay = qty * price + fees
        if outlay > self.cash + EPS:
            raise InsufficientCash(f"{symbol}: need {outlay:.2f}, have {self.cash:.2f}")
        self.cash -= outlay
        pos = self.positions.get(symbol)
        if pos is None:
            self.positions[symbol] = Position(qty, outlay, pd.Timestamp(date), index)
        else:
            pos.qty += qty
            pos.cost += outlay
        return self._record(date, symbol, "BUY", qty, price, fees, order_id, -outlay, reason)

    def sell(self, date, symbol: str, qty: int, price: float, fees: float, order_id: str, *, reason: str = "") -> Fill:
        self._check_trade(qty, price, fees, order_id)
        pos = self.positions.get(symbol)
        if pos is None or qty > pos.qty:
            raise UnownedSale(f"{symbol}: selling {qty}, own {0 if pos is None else pos.qty}")
        proceeds = qty * price - fees
        cost_out = pos.cost * qty / pos.qty
        pnl = proceeds - cost_out
        self.realized_pnl += pnl
        self.cash += proceeds
        self.closed_trades.append(
            {
                "symbol": symbol,
                "entry_date": pos.entry_date,
                "exit_date": pd.Timestamp(date),
                "qty": qty,
                "cost": cost_out,
                "proceeds": proceeds,
                "pnl": pnl,
                "return": pnl / cost_out if cost_out > 0 else 0.0,
                "reason": reason,
            }
        )
        pos.qty -= qty
        pos.cost -= cost_out
        if pos.qty == 0:
            del self.positions[symbol]
        return self._record(date, symbol, "SELL", qty, price, fees, order_id, proceeds, reason)

    def _check_trade(self, qty, price, fees, order_id) -> None:
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise LedgerError(f"quantity must be a positive whole number, got {qty!r}")
        if not (price > 0):
            raise LedgerError(f"price must be positive, got {price}")
        if fees < 0:
            raise LedgerError("negative fees")
        if order_id in self._applied_orders:
            raise LedgerError(f"order {order_id} already applied")

    def _record(self, date, symbol, side, qty, price, fees, order_id, cash_delta, reason) -> Fill:
        self._applied_orders.add(order_id)
        self.fees_paid += fees
        f = Fill(pd.Timestamp(date), symbol, side, qty, price, fees, reason, order_id)
        self.fills.append(f)
        self.journal.append(Journal(pd.Timestamp(date), side, symbol, qty if side == "BUY" else -qty, cash_delta, order_id))
        return f

    # ------------------------------------------------------------ corporate actions
    def apply_split(self, date, symbol: str, multiplier: float, ref_price_after: float) -> None:
        """Split/bonus: qty *= multiplier. Fractional entitlement is paid as cash in lieu
        at ``ref_price_after`` (an approximation of the actual sale by the company)."""
        pos = self.positions.get(symbol)
        if pos is None:
            return
        new_qty_f = pos.qty * multiplier
        new_qty = int(new_qty_f + 1e-9)
        frac = new_qty_f - new_qty
        self.journal.append(Journal(pd.Timestamp(date), "SPLIT", symbol, new_qty - pos.qty, 0.0, f"x{multiplier}"))
        if pos.stop_price is not None:
            pos.stop_price /= multiplier
        if new_qty == 0:
            # whole position became cash in lieu
            cash = round(frac * ref_price_after, 2)
            self.cash += cash
            self.realized_pnl += cash - pos.cost
            self.journal.append(Journal(pd.Timestamp(date), "CASH_IN_LIEU", symbol, -0, cash))
            del self.positions[symbol]
            return
        pos.qty = new_qty
        if frac > 1e-9:
            cash = round(frac * ref_price_after, 2)
            cost_part = pos.cost * frac / new_qty_f
            pos.cost -= cost_part
            self.cash += cash
            self.realized_pnl += cash - cost_part
            self.journal.append(Journal(pd.Timestamp(date), "CASH_IN_LIEU", symbol, 0, cash))

    def apply_dividend(self, date, symbol: str, per_share: float) -> float:
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        amt = round(pos.qty * per_share, 2)
        self.cash += amt
        self.dividends += amt
        self.journal.append(Journal(pd.Timestamp(date), "DIVIDEND", symbol, 0, amt))
        return amt

    def write_off(self, date, symbol: str, recovery_value: float) -> None:
        """Remove a position that can no longer be traded (e.g. delisted), crediting
        an assumed recovery value."""
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return
        rec = round(max(recovery_value, 0.0), 2)
        self.cash += rec
        pnl = rec - pos.cost
        self.realized_pnl += pnl
        self.closed_trades.append(
            {"symbol": symbol, "entry_date": pos.entry_date, "exit_date": pd.Timestamp(date), "qty": pos.qty,
             "cost": pos.cost, "proceeds": rec, "pnl": pnl, "return": pnl / pos.cost if pos.cost else 0.0,
             "reason": "write_off"}
        )
        self.journal.append(Journal(pd.Timestamp(date), "WRITE_OFF", symbol, -pos.qty, rec))

    # ------------------------------------------------------------------ queries
    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices[s] for s, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def check_invariants(self) -> None:
        cash = sum(j.cash_delta for j in self.journal)
        if abs(cash - self.cash) > 1e-4:
            raise LedgerError(f"cash mismatch: journal {cash:.6f} vs state {self.cash:.6f}")
        if self.cash < -EPS:
            raise LedgerError(f"negative cash {self.cash:.6f} in cash-only account")
        qty: dict[str, int] = {}
        for j in self.journal:
            if j.symbol is not None:
                qty[j.symbol] = qty.get(j.symbol, 0) + j.qty_delta
        for s, q in qty.items():
            held = self.positions[s].qty if s in self.positions else 0
            if q != held:
                raise LedgerError(f"{s}: journal qty {q} vs position {held}")
        for s, p in self.positions.items():
            if not isinstance(p.qty, int) or p.qty <= 0:
                raise LedgerError(f"{s}: invalid quantity {p.qty!r}")
            if p.cost < -EPS:
                raise LedgerError(f"{s}: negative cost basis")
