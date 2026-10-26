"""Performance metrics. All return figures are NET of modelled costs and PRE-TAX."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(-dd.min()) if len(dd) else 0.0


def cvar(returns: pd.Series, alpha: float = 0.05) -> float:
    """Expected shortfall of daily returns (positive number = loss)."""
    r = returns.dropna().to_numpy()
    if len(r) == 0:
        return 0.0
    q = np.quantile(r, alpha)
    tail = r[r <= q]
    return float(-tail.mean()) if len(tail) else 0.0


def sharpe(returns: pd.Series, rf_annual: float = 0.0) -> float:
    r = returns.dropna() - rf_annual / TRADING_DAYS
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS)) if sd and sd > 0 else 0.0


def sortino(returns: pd.Series, rf_annual: float = 0.0) -> float:
    r = returns.dropna() - rf_annual / TRADING_DAYS
    downside = np.sqrt((np.minimum(r, 0.0) ** 2).mean())
    return float(r.mean() / downside * math.sqrt(TRADING_DAYS)) if downside > 0 else 0.0


def summarize(result, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
              rf_annual: float = 0.0) -> dict:
    """Headline metrics for a BacktestResult over [start, end]."""
    eq = result.equity
    if start is not None:
        eq = eq[eq.index >= start]
    if end is not None:
        eq = eq[eq.index <= end]
    r = eq.pct_change().fillna(0.0)
    years = max(len(eq) - 1, 1) / TRADING_DAYS
    e0, e1 = float(eq.iloc[0]), float(eq.iloc[-1])
    cagr = (e1 / e0) ** (1 / years) - 1 if e0 > 0 and e1 > 0 else -1.0
    mdd = max_drawdown(eq)
    trades = result.trades
    if len(trades):
        tr = trades[(trades.exit_date >= eq.index[0]) & (trades.exit_date <= eq.index[-1])]
    else:
        tr = trades
    fills = result.fills
    if len(fills):
        fl = fills[(fills.date >= eq.index[0]) & (fills.date <= eq.index[-1])]
        traded = float((fl.qty * fl.price).sum())
        fees = float(fl.fees.sum())
    else:
        traded, fees = 0.0, 0.0
    wins = tr[tr.pnl > 0] if len(tr) else tr
    losses = tr[tr.pnl <= 0] if len(tr) else tr
    gross_win = float(wins.pnl.sum()) if len(tr) else 0.0
    gross_loss = float(-losses.pnl.sum()) if len(tr) else 0.0
    return {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "years": round(years, 2),
        "start_equity_inr": round(e0, 2),
        "end_equity_inr": round(e1, 2),
        "profit_inr": round(e1 - e0, 2),
        "total_return": e1 / e0 - 1 if e0 else 0.0,
        "cagr": cagr,
        "ann_vol": float(r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(r) > 1 else 0.0,
        "sharpe": sharpe(r, rf_annual),
        "sortino": sortino(r, rf_annual),
        "max_drawdown": mdd,
        "calmar": cagr / mdd if mdd > 0 else 0.0,
        "cvar_95_daily": cvar(r, 0.05),
        "n_trades": int(len(tr)),
        "win_rate": float(len(wins) / len(tr)) if len(tr) else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "expectancy_inr": float(tr.pnl.mean()) if len(tr) else 0.0,
        "fees_inr": round(fees, 2),
        "turnover_annual": traded / float(eq.mean()) / years if float(eq.mean()) > 0 else 0.0,
        "avg_exposure": float(result.exposure.loc[eq.index].mean()),
    }


def estimate_stcg_tax(trades: pd.DataFrame, rate: float = 0.20) -> pd.Series:
    """Rough short-term capital-gains estimate per Indian financial year (Apr-Mar).

    Assumes all trades are short term (<12 months holding), nets gains and losses within
    the FY, ignores loss carry-forward, the basic exemption and surcharge/cess. The 20%
    rate applies to listed equity STCG from 23 Jul 2024 (15% before); UNVERIFIED -
    consult a tax professional. Reported separately; never mixed into backtest returns.
    """
    if trades is None or len(trades) == 0:
        return pd.Series(dtype=float)
    fy = trades.exit_date.apply(lambda d: d.year if d.month >= 4 else d.year - 1)
    net = trades.groupby(fy).pnl.sum()
    return (net.clip(lower=0) * rate).rename("est_stcg_tax_inr")
