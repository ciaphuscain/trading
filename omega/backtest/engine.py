"""Reference event-driven daily backtester.

Timeline for each session i (all prices RAW unless stated):

1. Corporate actions effective at session i are applied (split -> share count,
   dividend -> cash). Pending orders are adjusted for splits.
2. Orders decided at the close of i-1 execute at the OPEN of i with slippage,
   participation caps, whole shares and cash checks. Sells execute before buys.
3. Protective stops are checked against the session's low; a gap through the stop
   fills at the (worse) open, never at the stop price.
4. Positions without a bar for ``delist_after`` sessions are written off at an
   assumed recovery fraction of their last close.
5. Positions are marked at the close (last available close if no bar).
6. After the close the strategy decides; the allocator sizes whole-share orders;
   the independent risk engine approves or rejects each one.

Correctness is preferred to speed; see docs/BACKTEST_ASSUMPTIONS.md.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..costs.model import CostModel, SlippageModel
from ..data.market import MarketData
from ..portfolio.allocator import AllocatorConfig, OrderIntent, allocate
from ..portfolio.ledger import Ledger
from ..risk.engine import OrderCheck, RiskEngine, RiskLimits
from ..strategies.base import BookView, Holding, Strategy


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 5000.0
    broker: str = "discount_zero_delivery"
    cost_schedule: str | None = None  # None = bundled NSE delivery schedule
    slippage: SlippageModel = field(default_factory=SlippageModel)
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    delist_after: int = 10
    delist_recovery: float = 0.5
    strict_pit: bool = False      # re-prepare the strategy on truncated data every session (slow reference mode)
    check_invariants: bool = True

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class BacktestResult:
    strategy: dict
    equity: pd.Series
    cash: pd.Series
    exposure: pd.Series
    fills: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    risk_log: pd.DataFrame
    costs: dict
    data_version: str
    cost_version: str
    config: dict

    def returns(self, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.Series:
        r = self.equity.pct_change().fillna(0.0)
        if start is not None:
            r = r[r.index >= start]
        if end is not None:
            r = r[r.index <= end]
        return r


@dataclass
class _Pending:
    order_id: str
    intent: OrderIntent


@dataclass(frozen=True)
class MarketArrays:
    """Numpy views of RAW market data shared by the backtester and the paper session."""

    dates: pd.DatetimeIndex
    sidx: dict
    O: np.ndarray
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    V: np.ndarray
    has_bar: np.ndarray
    C_ff: np.ndarray
    avgvol: np.ndarray
    last_bar_row: np.ndarray


def market_arrays(data: MarketData) -> MarketArrays:
    raw = data.raw
    n = len(data.dates)
    O, H, L, C, V = (raw.field(f).to_numpy(dtype=float) for f in ("open", "high", "low", "close", "volume"))
    has_bar = np.isfinite(C) & np.isfinite(O)
    last_bar_row = np.maximum.accumulate(np.where(has_bar, np.arange(n)[:, None], -1), axis=0)
    return MarketArrays(data.dates, {s: j for j, s in enumerate(data.symbols)}, O, H, L, C, V, has_bar,
                        raw.close.ffill().to_numpy(dtype=float),
                        raw.volume.rolling(20, min_periods=5).mean().to_numpy(dtype=float), last_bar_row)


def plan_orders(strategy: Strategy, i: int, book: BookView, market_value: float, m: MarketArrays,
                costs: CostModel, cfg: "BacktestConfig", risk: RiskEngine):
    """Decision step after the close of session i: strategy -> allocator -> risk engine.

    Shared verbatim by the backtester and the paper session so both apply identical rules.
    Returns (approved [(order_id, intent)], rejected [(order_id, intent, reason)]).
    """
    d = m.dates[i]
    target = strategy.decide(i, book)
    if target.no_change:
        return [], []
    need = set(target.weights) | set(book.holdings)
    prices = {s: float(m.C_ff[i, m.sidx[s]]) for s in need if np.isfinite(m.C_ff[i, m.sidx[s]])}
    intents = allocate(target, book, prices, d, costs, cfg.allocator)
    gross, cash_avail = market_value, book.cash
    approved, rejected = [], []
    for it in intents:
        oid = f"{strategy.name}-{i}-{it.symbol}-{it.side}"
        j = m.sidx[it.symbol]
        lb = m.last_bar_row[i, j]
        held_qty = book.holdings[it.symbol].qty if it.symbol in book.holdings else 0
        av = float(m.avgvol[i, j]) if np.isfinite(m.avgvol[i, j]) else 0.0
        chk = OrderCheck(oid, it.symbol, it.side, it.qty, it.ref_price, float(m.C_ff[i, j]),
                         m.dates[lb] if lb >= 0 else None, av, held_qty, it.stop_frac)
        dec = risk.check(chk, equity=book.equity, cash=cash_avail, gross_exposure=gross,
                         position_value=held_qty * m.C_ff[i, j], decision_time=d)
        if dec.approved:
            approved.append((oid, it))
            val = it.qty * it.ref_price
            sign = 1 if it.side == "BUY" else -1
            gross += sign * val
            cash_avail -= sign * val
        else:
            rejected.append((oid, it, dec.reason))
    return approved, rejected


def run_backtest(strategy: Strategy, data: MarketData, cfg: BacktestConfig = BacktestConfig()) -> BacktestResult:
    m = market_arrays(data)
    dates, sidx = m.dates, m.sidx
    n = len(dates)
    O, L, V, has_bar, C_ff = m.O, m.L, m.V, m.has_bar, m.C_ff

    actions_at: dict[int, list] = defaultdict(list)
    for a in data.actions.itertuples(index=False):
        pos = dates.searchsorted(pd.Timestamp(a.ex_date))
        if pos < n and a.symbol in sidx:
            actions_at[pos].append(a)

    costs = CostModel(cfg.broker, cfg.cost_schedule)
    slip = cfg.slippage
    ledger = Ledger(cfg.initial_cash)
    risk = RiskEngine(cfg.risk, cfg.initial_cash)
    if not cfg.strict_pit:
        strategy.prepare(data)

    equity = np.zeros(n)
    cash_s = np.zeros(n)
    expo = np.zeros(n)
    events: list[dict] = []
    cost_tot: dict[str, float] = defaultdict(float)
    pending: list[_Pending] = []
    missing_run = defaultdict(int)

    def ev(i, kind, symbol=None, **kw):
        events.append({"date": dates[i], "kind": kind, "symbol": symbol, **kw})

    def charge(i, side, value, first_sell):
        cb = costs.charges(side, value, dates[i], first_sell_of_scrip_today=first_sell)
        return cb

    for i in range(n):
        d = dates[i]
        # 1. corporate actions -------------------------------------------------
        for a in actions_at.get(i, []):
            j = sidx[a.symbol]
            if a.kind == "split":
                ref = O[i, j] if has_bar[i, j] else C_ff[i, j] / a.value
                ledger.apply_split(d, a.symbol, float(a.value), float(ref))
                for p in pending:
                    if p.intent.symbol == a.symbol:
                        it = p.intent
                        p.intent = OrderIntent(it.symbol, it.side, int(it.qty * a.value), it.reason, it.stop_frac,
                                               it.ref_price / a.value)
                ev(i, "split", a.symbol, value=float(a.value))
            elif a.kind == "dividend":
                amt = ledger.apply_dividend(d, a.symbol, float(a.value))
                if amt:
                    ev(i, "dividend", a.symbol, amount=amt)

        # Yesterday's closing equity. (Re-marking current positions at yesterday's closes would be
        # wrong on a split ex-date: post-split quantity x pre-split price inflates the drawdown peak.)
        risk.start_day(d, float(equity[i - 1]) if i > 0 else cfg.initial_cash)

        # 2. execute pending orders at the open ----------------------------------
        sold_today: set[str] = set()
        bought_today: set[str] = set()
        for p in sorted(pending, key=lambda p: 0 if p.intent.side == "SELL" else 1):
            it = p.intent
            j = sidx[it.symbol]
            if not has_bar[i, j]:
                ev(i, "order_rejected", it.symbol, order_id=p.order_id, reason="no_bar")
                continue
            vol = V[i, j]
            cap = int(slip.max_participation * vol) if np.isfinite(vol) else 0
            qty = it.qty
            if it.side == "SELL":
                held = ledger.positions[it.symbol].qty if it.symbol in ledger.positions else 0
                qty = min(qty, held)
            if qty > cap:
                ev(i, "partial_fill", it.symbol, order_id=p.order_id, wanted=qty, filled=cap)
                qty = cap
            if qty <= 0:
                ev(i, "order_rejected", it.symbol, order_id=p.order_id, reason="no_quantity_or_liquidity")
                continue
            px = slip.price(O[i, j], it.side, qty, vol)
            if it.side == "SELL":
                cb = charge(i, "SELL", qty * px, it.symbol not in sold_today)
                ledger.sell(d, it.symbol, qty, px, cb.total, p.order_id, reason=it.reason)
                sold_today.add(it.symbol)
            else:
                cb = charge(i, "BUY", qty * px, False)
                if qty * px + cb.total > ledger.cash:
                    # A broker's risk system rejects an unaffordable cash-account buy; it does not
                    # shrink it. (Shrinking here made the backtest diverge from the paper broker.)
                    ev(i, "order_rejected", it.symbol, order_id=p.order_id, reason="insufficient_cash_at_open")
                    continue
                existed = it.symbol in ledger.positions
                ledger.buy(d, it.symbol, qty, px, cb.total, p.order_id, index=i, reason=it.reason)
                if it.stop_frac and not existed:
                    ledger.positions[it.symbol].stop_price = it.ref_price * (1 - it.stop_frac)
                bought_today.add(it.symbol)
            for k, v in cb.as_dict().items():
                cost_tot[k] += v
            slip_cost = abs(px - O[i, j]) * qty
            cost_tot["slippage"] += slip_cost
        pending = []

        # 3. protective stops -----------------------------------------------------
        for s in list(ledger.positions):
            pos = ledger.positions[s]
            j = sidx[s]
            if pos.stop_price is None or not has_bar[i, j] or L[i, j] > pos.stop_price:
                continue
            gap = O[i, j] <= pos.stop_price
            ref = O[i, j] if gap else pos.stop_price
            qty = pos.qty
            px = slip.price(ref, "SELL", qty, V[i, j])
            cb = charge(i, "SELL", qty * px, s not in sold_today)
            ledger.sell(d, s, qty, px, cb.total, f"STOP-{i}-{s}", reason="stop_gap" if gap else "stop")
            sold_today.add(s)
            for k, v in cb.as_dict().items():
                cost_tot[k] += v
            cost_tot["slippage"] += abs(px - ref) * qty
            ev(i, "stop_gap" if gap else "stop", s, price=px)

        # 4. delisting / long suspension -------------------------------------------
        for s in list(ledger.positions):
            j = sidx[s]
            missing_run[s] = 0 if has_bar[i, j] else missing_run[s] + 1
            if missing_run[s] >= cfg.delist_after:
                pos = ledger.positions[s]
                ledger.write_off(d, s, pos.qty * C_ff[i, j] * cfg.delist_recovery)
                ev(i, "write_off", s, recovery=cfg.delist_recovery)

        # 5. mark to market ---------------------------------------------------------
        marks = {s: C_ff[i, sidx[s]] for s in ledger.positions}
        mv = ledger.market_value(marks)
        equity[i] = ledger.cash + mv
        cash_s[i] = ledger.cash
        expo[i] = mv / equity[i] if equity[i] > 0 else 0.0
        risk.update_equity(equity[i])
        if cfg.check_invariants:
            ledger.check_invariants()

        # 6. decide for the next session ------------------------------------------
        if i == n - 1:
            break
        if cfg.strict_pit:
            strategy.prepare(data.truncate(i + 1))
        book = BookView(
            equity=float(equity[i]),
            cash=float(ledger.cash),
            holdings={s: Holding(p.qty, p.entry_index, p.avg_cost) for s, p in ledger.positions.items()},
        )
        approved, rejected = plan_orders(strategy, i, book, mv, m, costs, cfg, risk)
        pending.extend(_Pending(oid, it) for oid, it in approved)
        for oid, it, reason in rejected:
            ev(i, "risk_rejected", it.symbol, order_id=oid, reason=reason)

    idx = dates
    fills = pd.DataFrame([f.__dict__ for f in ledger.fills])
    trades = pd.DataFrame(ledger.closed_trades)
    cost_tot = {k: round(v, 2) for k, v in cost_tot.items()}
    cost_tot["explicit_total"] = round(sum(v for k, v in cost_tot.items() if k not in ("total", "slippage")), 2)
    cost_tot.pop("total", None)
    cost_tot["dividends"] = round(ledger.dividends, 2)
    return BacktestResult(
        strategy=strategy.describe(),
        equity=pd.Series(equity, index=idx, name="equity"),
        cash=pd.Series(cash_s, index=idx, name="cash"),
        exposure=pd.Series(expo, index=idx, name="exposure"),
        fills=fills,
        trades=trades,
        events=pd.DataFrame(events),
        risk_log=pd.DataFrame(risk.log),
        costs=cost_tot,
        data_version=data.version(),
        cost_version=costs.version_id(dates[-1]),
        config={"initial_cash": cfg.initial_cash, "broker": cfg.broker, "cost_schedule": costs.schedule_path,
                "slippage": asdict(cfg.slippage),
                "allocator": asdict(cfg.allocator), "risk": asdict(cfg.risk), "delist_after": cfg.delist_after,
                "delist_recovery": cfg.delist_recovery},
    )
