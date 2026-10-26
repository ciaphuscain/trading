"""Tier-1 candidate strategies. See docs/STRATEGIES.md for the full specification,
literature references and deviations from the source papers."""

from __future__ import annotations

import numpy as np

from ..data.market import MarketData
from . import indicators as ind
from .base import BookView, Strategy, Target, equal_weights, filled, top_k


class CashStrategy(Strategy):
    """OMEGA-C1: never trade. The null hypothesis every candidate must beat after costs."""

    name = "C1_cash"

    def _prepare(self, data: MarketData) -> None:
        pass

    def decide(self, i: int, book: BookView) -> Target:
        return Target() if book.holdings else Target.hold()


class BuyAndHold(Strategy):
    """Benchmark: on the first eligible day buy the ``n`` most liquid affordable
    stocks equally and never trade again (except forced exits handled by the engine)."""

    name = "BH_buy_and_hold"

    def __init__(self, n: int = 2, **kw):
        super().__init__(n=n, **kw)
        self.n = n
        self.warmup = 21

    def _prepare(self, data: MarketData) -> None:
        pass

    def decide(self, i: int, book: BookView) -> Target:
        if book.holdings or i < self.warmup:
            return Target.hold()
        mask = self.eligible(i, book.equity, self.n)
        picks = top_k(self._liq[i], mask, self.n)
        return Target(weights=equal_weights([str(self._symbols[j]) for j in picks]))


class CrossSectionalMomentum(Strategy):
    """OMEGA-M1 (Jegadeesh & Titman 1993, long-only top bucket).

    score_i,t = P_i,t-skip / P_i,t-lookback - 1        (optionally / sigma_i,t)
    Every ``rebalance`` sessions: hold the ``top_n`` eligible names; an existing
    holding is kept while its rank <= top_n + ``hysteresis`` (reduces turnover).
    Optional market filter: no new longs when the equal-weight universe index is
    below its ``trend_filter``-day SMA (Moskowitz-Ooi-Pedersen style regime gate).
    """

    name = "M1_xs_momentum"

    def __init__(self, lookback=126, skip=21, top_n=2, rebalance=21, hysteresis=2,
                 vol_adjust=False, trend_filter=0, **kw):
        super().__init__(lookback=lookback, skip=skip, top_n=top_n, rebalance=rebalance,
                         hysteresis=hysteresis, vol_adjust=vol_adjust, trend_filter=trend_filter, **kw)
        self.lookback, self.skip, self.top_n = lookback, skip, top_n
        self.rebalance, self.hysteresis = rebalance, hysteresis
        self.vol_adjust, self.trend_filter = vol_adjust, trend_filter
        self.warmup = max(lookback, trend_filter, 63) + 1

    def _prepare(self, data: MarketData) -> None:
        c = filled(data.adj.close)
        score = ind.total_return(c, self.lookback, self.skip)
        if self.vol_adjust:
            score = score / ind.realized_vol(c, 63)
        self._score = score.to_numpy()
        self._risk_on = _market_gate(c, self.trend_filter)

    def decide(self, i: int, book: BookView) -> Target:
        if i < self.warmup or (i - self.warmup) % self.rebalance != 0:
            return Target.hold()
        if not self._risk_on[i]:
            return Target(reasons={"*": "market_filter_off"})
        score = self._score[i]
        mask = self.eligible(i, book.equity, self.top_n)
        cand = mask.copy()
        for s in book.holdings:
            cand[self._sym_idx[s]] = True  # held names compete for the hysteresis band
        ranked = [str(self._symbols[j]) for j in top_k(score, cand, self.top_n + self.hysteresis)]
        keep = [s for s in ranked if s in book.holdings][: self.top_n]
        fresh = [str(self._symbols[j]) for j in top_k(score, mask, self.top_n + len(keep))]
        new = [s for s in fresh if s not in keep][: self.top_n - len(keep)]
        weights: dict[str, float | None] = {s: None for s in keep}
        weights.update({s: 1.0 / self.top_n for s in new})
        return Target(weights=weights)


class TimeSeriesTrend(Strategy):
    """OMEGA-M2: per-stock trend following.

    In trend if close > SMA(fast) > SMA(slow) and the ``abs_lookback`` return > 0
    (absolute momentum). Entries ranked by volatility-adjusted ``abs_lookback`` return.
    Exit on trend break (close < SMA(fast)) or ATR stop.
    """

    name = "M2_ts_trend"

    def __init__(self, fast=50, slow=200, abs_lookback=126, top_n=2, atr_mult=3.0, **kw):
        super().__init__(fast=fast, slow=slow, abs_lookback=abs_lookback, top_n=top_n, atr_mult=atr_mult, **kw)
        self.fast, self.slow, self.abs_lookback, self.top_n, self.atr_mult = fast, slow, abs_lookback, top_n, atr_mult
        self.warmup = max(slow, abs_lookback) + 1

    def _prepare(self, data: MarketData) -> None:
        c = filled(data.adj.close)
        h, l = filled(data.adj.high), filled(data.adj.low)
        f, s = ind.sma(c, self.fast), ind.sma(c, self.slow)
        mom = ind.total_return(c, self.abs_lookback)
        self._in_trend = ((c > f) & (f > s) & (mom > 0)).to_numpy()
        self._exit = (c < f).to_numpy()
        self._score = (mom / ind.realized_vol(c, 63)).to_numpy()
        self._stopfrac = (self.atr_mult * ind.atr(h, l, c, 20) / c).to_numpy()

    def decide(self, i: int, book: BookView) -> Target:
        if i < self.warmup:
            return Target.hold()
        return _signal_book(self, i, book, self._in_trend[i], self._exit[i], self._score[i], self._stopfrac[i])


class ConditionalMeanReversion(Strategy):
    """OMEGA-R1: buy short-term oversold pullbacks ONLY inside long-term uptrends.

    Entry: RSI(rsi_n) < entry_rsi and close > SMA(trend_n). Rank: lowest RSI.
    Exit: RSI > exit_rsi, or close > SMA(exit_sma), or held >= max_hold sessions,
    or ATR stop. Trend condition avoids "buying because it fell".
    """

    name = "R1_cond_mean_reversion"

    def __init__(self, rsi_n=2, entry_rsi=10.0, exit_rsi=70.0, trend_n=200, exit_sma=5, max_hold=10,
                 top_n=2, atr_mult=3.0, **kw):
        super().__init__(rsi_n=rsi_n, entry_rsi=entry_rsi, exit_rsi=exit_rsi, trend_n=trend_n,
                         exit_sma=exit_sma, max_hold=max_hold, top_n=top_n, atr_mult=atr_mult, **kw)
        self.rsi_n, self.entry_rsi, self.exit_rsi = rsi_n, entry_rsi, exit_rsi
        self.trend_n, self.exit_sma, self.max_hold, self.top_n, self.atr_mult = trend_n, exit_sma, max_hold, top_n, atr_mult
        self.warmup = trend_n + 1

    def _prepare(self, data: MarketData) -> None:
        c = filled(data.adj.close)
        h, l = filled(data.adj.high), filled(data.adj.low)
        r = ind.rsi(c, self.rsi_n)
        self._entry = ((r < self.entry_rsi) & (c > ind.sma(c, self.trend_n))).to_numpy()
        self._exit = ((r > self.exit_rsi) | (c > ind.sma(c, self.exit_sma))).to_numpy()
        self._score = (-r).to_numpy()
        self._stopfrac = (self.atr_mult * ind.atr(h, l, c, 10) / c).to_numpy()

    def decide(self, i: int, book: BookView) -> Target:
        if i < self.warmup:
            return Target.hold()
        return _signal_book(self, i, book, self._entry[i], self._exit[i], self._score[i], self._stopfrac[i],
                            max_hold=self.max_hold)


class DonchianBreakout(Strategy):
    """OMEGA-B1: Donchian channel breakout (turtle-style, long only).

    Entry: close > max(high over previous ``entry_n`` sessions) and, if
    ``volume_mult`` > 0, volume > volume_mult x 20-day average volume.
    Exit: close < min(low over previous ``exit_n`` sessions) or ATR stop.
    Rank: breakout distance in ATR units.
    """

    name = "B1_donchian_breakout"

    def __init__(self, entry_n=55, exit_n=20, volume_mult=0.0, top_n=2, atr_mult=2.0, **kw):
        super().__init__(entry_n=entry_n, exit_n=exit_n, volume_mult=volume_mult, top_n=top_n, atr_mult=atr_mult, **kw)
        self.entry_n, self.exit_n, self.volume_mult, self.top_n, self.atr_mult = entry_n, exit_n, volume_mult, top_n, atr_mult
        self.warmup = max(entry_n, exit_n, 21) + 1

    def _prepare(self, data: MarketData) -> None:
        c = filled(data.adj.close)
        h, l = filled(data.adj.high), filled(data.adj.low)
        v = data.adj.volume
        upper = ind.rolling_max(h, self.entry_n)
        lower = ind.rolling_min(l, self.exit_n)
        a = ind.atr(h, l, c, 20)
        entry = c > upper
        if self.volume_mult > 0:
            entry &= v > self.volume_mult * v.rolling(20, min_periods=20).mean().shift(1)
        self._entry = entry.to_numpy()
        self._exit = (c < lower).to_numpy()
        self._score = ((c - upper) / a).to_numpy()
        self._stopfrac = (self.atr_mult * a / c).to_numpy()

    def decide(self, i: int, book: BookView) -> Target:
        if i < self.warmup:
            return Target.hold()
        return _signal_book(self, i, book, self._entry[i], self._exit[i], self._score[i], self._stopfrac[i])


# ---------------------------------------------------------------------------- helpers


def _market_gate(close, n: int) -> np.ndarray:
    """True when the equal-weight universe index is above its n-day SMA (n=0: always on)."""
    if n <= 0:
        return np.ones(len(close), dtype=bool)
    rets = close.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    idx = (1 + rets).cumprod()
    gate = idx > idx.rolling(n, min_periods=n).mean()
    return gate.to_numpy()


def _signal_book(strat: Strategy, i: int, book: BookView, entry_row, exit_row, score_row, stop_row,
                 max_hold: int | None = None) -> Target:
    """Shared event logic for signal strategies: exits first, then fill free slots."""
    syms = strat._symbols
    weights: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for s, h in book.holdings.items():
        j = strat._sym_idx[s]
        if bool(exit_row[j]):
            reasons[s] = "exit_signal"
            continue
        if max_hold is not None and i - h.entry_index >= max_hold:
            reasons[s] = "time_exit"
            continue
        weights[s] = None
    free = strat.top_n - len(weights)
    stops: dict[str, float] = {}
    if free > 0:
        mask = strat.eligible(i, book.equity, strat.top_n) & np.nan_to_num(entry_row, nan=0).astype(bool)
        mask &= ~np.isin(strat._symbols, list(book.holdings))
        for j in top_k(np.asarray(score_row, dtype=float), mask, free):
            s = str(syms[j])
            weights[s] = 1.0 / strat.top_n
            sf = stop_row[j]
            if np.isfinite(sf) and sf > 0:
                stops[s] = float(min(sf, 0.5))
            reasons[s] = "entry_signal"
    if not weights and not book.holdings:
        return Target.hold()
    return Target(weights=weights, stops=stops, reasons=reasons)


REGISTRY = {
    cls.name: cls
    for cls in (CashStrategy, BuyAndHold, CrossSectionalMomentum, TimeSeriesTrend, ConditionalMeanReversion, DonchianBreakout)
}
