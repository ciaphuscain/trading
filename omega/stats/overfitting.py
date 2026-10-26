"""Statistical defences against backtest overfitting.

References (see docs/RESEARCH_METHODS.md):
* Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier" - PSR.
* Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio" - DSR.
* Bailey, Borwein, Lopez de Prado & Zhu (2017), "The Probability of Backtest
  Overfitting" - CSCV / PBO.
* Politis & Romano (1994) - stationary bootstrap.
* Holm (1979); Benjamini & Hochberg (1995) - multiple-testing corrections, as
  advocated for factor research by Harvey, Liu & Zhu (2016).
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def _moments(r: np.ndarray) -> tuple[float, float, float]:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    sd = r.std(ddof=1)
    sr = r.mean() / sd if sd > 0 else 0.0
    skew = float(stats.skew(r, bias=False)) if len(r) > 2 and sd > 0 else 0.0
    kurt = float(stats.kurtosis(r, fisher=False, bias=False)) if len(r) > 3 and sd > 0 else 3.0
    return sr, skew, kurt


def probabilistic_sharpe_ratio(returns, sr_benchmark: float = 0.0) -> float:
    """P[true per-period SR > sr_benchmark] given sample length, skew and kurtosis.

    PSR = Phi( (SR - SR*) sqrt(n-1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2) ), SR non-annualised.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 3:
        return float("nan")
    sr, g3, g4 = _moments(r)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    if denom <= 0:
        return float("nan")
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max SR] of n_trials independent zero-skill strategies (False Strategy Theorem)."""
    if n_trials <= 1:
        return 0.0
    z1 = stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(max(var_sr, 0.0)) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe_ratio(returns, n_trials: int, var_trial_sr: float) -> float:
    """PSR against the SR expected from the best of ``n_trials`` unskilled attempts.

    var_trial_sr: cross-sectional variance of the (per-period) Sharpe ratios of all
    trials actually run. The experiment registry supplies both quantities so that the
    selection bias of every attempted configuration is charged.
    """
    return probabilistic_sharpe_ratio(returns, expected_max_sharpe(n_trials, var_trial_sr))


def stationary_bootstrap_ci(returns, stat=None, n_boot: int = 2000, mean_block: int = 20,
                            alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """Politis-Romano stationary bootstrap CI for a statistic of a return series.

    Default statistic: annualised Sharpe ratio. Returns (point, lower, upper).
    Blocks preserve volatility clustering and short-range autocorrelation.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if stat is None:
        def stat(x):
            s = x.std(ddof=1)
            return x.mean() / s * math.sqrt(252) if s > 0 else 0.0
    point = float(stat(r))
    if n < 10:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    out = np.empty(n_boot)
    t = np.arange(n)
    for b in range(n_boot):
        # Vectorised stationary bootstrap: geometric block lengths, random block starts.
        new_block = rng.random(n) < p
        new_block[0] = True
        block_id = np.cumsum(new_block) - 1
        block_pos = np.flatnonzero(new_block)
        starts = rng.integers(n, size=len(block_pos))
        idx = (starts[block_id] + t - block_pos[block_id]) % n
        out[b] = stat(r[idx])
    lo, hi = np.quantile(out, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)


def pbo_cscv(returns_matrix: np.ndarray, n_splits: int = 10, metric=None) -> dict:
    """Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation.

    returns_matrix: T x N per-period returns of N configurations over the SAME dates.
    For every way of choosing half of the S blocks as in-sample, pick the configuration
    with the best IS metric and record its relative OOS rank. PBO is the fraction of
    splits where the IS-best configuration ranks below the OOS median (logit <= 0).
    """
    M = np.asarray(returns_matrix, dtype=float)
    T, N = M.shape
    if N < 2:
        raise ValueError("need at least two configurations")
    if n_splits % 2 or n_splits < 2:
        raise ValueError("n_splits must be even")
    if metric is None:
        def metric(x):
            sd = x.std(axis=0, ddof=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(sd > 0, x.mean(axis=0) / sd, 0.0)
    blocks = np.array_split(np.arange(T), n_splits)
    logits, degr = [], []
    for combo in itertools.combinations(range(n_splits), n_splits // 2):
        is_idx = np.concatenate([blocks[k] for k in combo])
        oos_idx = np.concatenate([blocks[k] for k in range(n_splits) if k not in combo])
        m_is, m_oos = metric(M[is_idx]), metric(M[oos_idx])
        best = int(np.argmax(m_is))
        rank = stats.rankdata(m_oos)[best] / (N + 1)  # relative rank in (0,1)
        logits.append(math.log(rank / (1 - rank)))
        degr.append((m_is[best], m_oos[best]))
    logits = np.array(logits)
    d = np.array(degr)
    slope = float(np.polyfit(d[:, 0], d[:, 1], 1)[0]) if len(d) > 2 and np.ptp(d[:, 0]) > 0 else float("nan")
    return {
        "pbo": float((logits <= 0).mean()),
        "n_combinations": int(len(logits)),
        "median_logit": float(np.median(logits)),
        "is_oos_slope": slope,
        "mean_is_best": float(d[:, 0].mean()),
        "mean_oos_of_is_best": float(d[:, 1].mean()),
    }


def holm(pvalues) -> np.ndarray:
    """Holm step-down adjusted p-values (family-wise error control)."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, (m - k) * p[i])
        adj[i] = min(running, 1.0)
    return adj


def benjamini_hochberg(pvalues) -> np.ndarray:
    """BH adjusted p-values (false discovery rate control)."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    order = np.argsort(p)[::-1]
    adj = np.empty(m)
    running = 1.0
    for k, i in enumerate(order):
        rank = m - k
        running = min(running, p[i] * m / rank)
        adj[i] = running
    return adj


def sharpe_pvalue(returns, sr_benchmark: float = 0.0) -> float:
    """One-sided p-value for H0: SR <= sr_benchmark (1 - PSR)."""
    psr = probabilistic_sharpe_ratio(returns, sr_benchmark)
    return float("nan") if math.isnan(psr) else 1.0 - psr
