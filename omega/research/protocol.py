"""Research protocol: locked holdout, walk-forward selection, tournament, promotion gate.

Walk-forward design (see docs/RESEARCH_METHODS.md):
Each configuration in a grid is backtested ONCE over the research period. Because the
backtester is causal, the return on day t of a continuous run is exactly what that
configuration would have earned live. Folds then select, on each training window,
the configuration with the best training objective and take its returns on the
following test window. The stitched test-window returns form the out-of-sample
(OOS) record. Costs of switching between configurations at fold boundaries are not
charged (documented limitation; small for low-turnover strategies).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..backtest.engine import BacktestConfig, BacktestResult, run_backtest
from ..data.market import MarketData
from ..stats import metrics as M
from ..stats import overfitting as OF
from .registry import Registry


# ----------------------------------------------------------------------- holdout lock
@dataclass(frozen=True)
class ResearchSplit:
    """Everything on/after ``holdout_start`` is the locked final holdout."""

    holdout_start: pd.Timestamp

    def research(self, data: MarketData) -> MarketData:
        return data.window(None, self.holdout_start - pd.Timedelta(days=1))

    def open_holdout(self, data: MarketData, registry: Registry, family: str, reason: str,
                     allow_repeat: bool = False) -> MarketData:
        prior = registry.holdout_accesses(family)
        if prior and not allow_repeat:
            raise PermissionError(
                f"holdout for family '{family}' already opened {len(prior)} time(s); results on it are no longer "
                "an unbiased estimate. Pass allow_repeat=True to proceed (this is recorded)."
            )
        registry.record_holdout_access(family=family, reason=reason, data_version=data.version())
        return data


def grid(**axes) -> list[dict]:
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(axes[k] for k in keys))]


def period_sharpe(r: pd.Series) -> float:
    r = r.dropna()
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd and sd > 0 else 0.0


# ------------------------------------------------------------------------ walk-forward
@dataclass
class WalkForwardResult:
    family: str
    configs: list[dict]
    runs: list[BacktestResult]
    folds: pd.DataFrame
    oos_returns: pd.Series
    returns_matrix: pd.DataFrame
    pbo: dict
    grid_stats: dict
    oos_metrics: dict = field(default_factory=dict)


def walk_forward(family: str, factory, configs: list[dict], data: MarketData, *,
                 train_days: int = 504, test_days: int = 126, objective: str = "sharpe",
                 cfg: BacktestConfig = BacktestConfig(), registry: Registry | None = None,
                 hypothesis: str = "") -> WalkForwardResult:
    if not configs:
        raise ValueError("empty configuration grid")
    runs = []
    for p in configs:
        res = run_backtest(factory(**p), data, cfg)
        runs.append(res)
        if registry is not None:
            r = res.returns()
            registry.record_trial(family=family, strategy=res.strategy["strategy"], params=p,
                                  data_version=res.data_version, cost_version=res.cost_version,
                                  metrics=_clean(M.summarize(res)), period_sr=period_sharpe(r), hypothesis=hypothesis,
                                  stage="walk_forward")
    R = pd.concat([r.returns().rename(i) for i, r in enumerate(runs)], axis=1).fillna(0.0)
    n = len(R)
    folds, oos = [], []
    start = train_days
    while start < n:
        end = min(start + test_days, n)
        tr, te = R.iloc[start - train_days:start], R.iloc[start:end]
        if objective == "sharpe":
            score = tr.apply(period_sharpe)
        elif objective == "return":
            score = (1 + tr).prod() - 1
        else:
            raise ValueError(objective)
        best = int(score.idxmax())
        oos.append(te[best])
        folds.append({"train_start": tr.index[0], "test_start": te.index[0], "test_end": te.index[-1],
                      "chosen": best, "params": configs[best], "is_sharpe_ann": period_sharpe(tr[best]) * math.sqrt(252),
                      "oos_sharpe_ann": period_sharpe(te[best]) * math.sqrt(252),
                      "oos_return": float((1 + te[best]).prod() - 1)})
        start = end
    oos_r = pd.concat(oos) if oos else pd.Series(dtype=float)
    fdf = pd.DataFrame(folds)
    full_sr = R.apply(period_sharpe) * math.sqrt(252)
    gstats = {"n_configs": len(configs), "sharpe_median": float(full_sr.median()),
              "sharpe_min": float(full_sr.min()), "sharpe_max": float(full_sr.max()),
              "frac_positive": float((full_sr > 0).mean())}
    pbo = OF.pbo_cscv(R.to_numpy(), n_splits=10) if len(configs) >= 2 and n >= 200 else {"pbo": float("nan")}
    return WalkForwardResult(family, configs, runs, fdf, oos_r, R, pbo, gstats)


def sensitivity_table(wf: "WalkForwardResult") -> pd.DataFrame:
    """Marginal full-period Sharpe for each value of each parameter across the grid.

    A robust strategy shows a plateau (similar Sharpe across neighbouring values); a spike at
    one value is a warning sign of overfitting. Uses the same runs as the walk-forward, so it
    costs no extra trials.
    """
    sr = wf.returns_matrix.apply(period_sharpe) * math.sqrt(252)
    rows = []
    keys = sorted({k for c in wf.configs for k in c})
    for k in keys:
        vals = sorted({repr(c.get(k)) for c in wf.configs})
        if len(vals) < 2:
            continue
        for v in vals:
            idx = [i for i, c in enumerate(wf.configs) if repr(c.get(k)) == v]
            x = sr.iloc[idx]
            rows.append({"param": k, "value": v, "n": len(idx), "sharpe_mean": float(x.mean()),
                         "sharpe_min": float(x.min()), "sharpe_max": float(x.max())})
    return pd.DataFrame(rows)


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = str(v)
        else:
            out[k] = v
    return out


# ------------------------------------------------------------------------- evidence
@dataclass(frozen=True)
class GateConfig:
    min_dsr: float = 0.95
    max_pbo: float = 0.5
    min_trades: int = 30
    max_drawdown: float = 0.35
    min_oos_years: float = 2.0


def evidence_for(returns: pd.Series, *, n_trials: int, var_trial_sr: float, benchmark_returns: pd.Series | None,
                 n_trades: int, pbo: float, seed: int = 0) -> dict:
    r = returns.dropna()
    eq = (1 + r).cumprod()
    sr_point, lo, hi = OF.stationary_bootstrap_ci(r.to_numpy(), n_boot=1000, seed=seed)
    ev = {
        "years": len(r) / 252,
        "sharpe_ann": sr_point,
        "sharpe_ci95": [lo, hi],
        "psr_vs_0": OF.probabilistic_sharpe_ratio(r.to_numpy(), 0.0),
        "dsr": OF.deflated_sharpe_ratio(r.to_numpy(), max(n_trials, 1), var_trial_sr),
        "n_trials_charged": n_trials,
        "total_return": float(eq.iloc[-1] - 1) if len(eq) else 0.0,
        "max_drawdown": M.max_drawdown(eq) if len(eq) else 0.0,
        "n_trades": n_trades,
        "pbo": pbo,
    }
    if benchmark_returns is not None:
        b = benchmark_returns.reindex(r.index).fillna(0.0)
        diff = r - b
        ev["benchmark_total_return"] = float((1 + b).prod() - 1)
        ev["psr_excess_vs_benchmark"] = OF.probabilistic_sharpe_ratio(diff.to_numpy(), 0.0)
    return ev


def promotion_gate(ev: dict, gate: GateConfig = GateConfig()) -> tuple[str, list[str]]:
    """Conservative, rule-based decision. Any failed rule -> REJECT (hold cash/passive)."""
    reasons = []
    if ev["years"] < gate.min_oos_years:
        reasons.append(f"OOS history {ev['years']:.1f}y < {gate.min_oos_years}y")
    if not (ev["dsr"] >= gate.min_dsr):
        reasons.append(f"DSR {ev['dsr']:.3f} < {gate.min_dsr} after {ev['n_trials_charged']} trials")
    lo = ev["sharpe_ci95"][0]
    if not (lo > 0):
        reasons.append(f"bootstrap 95% CI of Sharpe includes 0 (lower {lo:.2f})")
    if not (ev["pbo"] <= gate.max_pbo):
        reasons.append(f"PBO {ev['pbo']:.2f} > {gate.max_pbo}")
    if ev["n_trades"] < gate.min_trades:
        reasons.append(f"{ev['n_trades']} trades < {gate.min_trades}")
    if ev["max_drawdown"] > gate.max_drawdown:
        reasons.append(f"max drawdown {ev['max_drawdown']:.0%} > {gate.max_drawdown:.0%}")
    if ev["total_return"] <= 0:
        reasons.append("does not beat cash (net OOS return <= 0)")
    if "benchmark_total_return" in ev and ev["total_return"] <= ev["benchmark_total_return"]:
        reasons.append("does not beat the passive benchmark")
    return ("PROMOTE_TO_PAPER" if not reasons else "REJECT"), reasons
