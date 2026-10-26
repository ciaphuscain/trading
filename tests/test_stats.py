import math

import numpy as np
import pandas as pd
import pytest

from omega.stats import metrics as M
from omega.stats import overfitting as OF


def test_max_drawdown_and_cvar_hand_values():
    eq = pd.Series([100, 120, 90, 95, 130, 117.0])
    assert M.max_drawdown(eq) == pytest.approx(0.25)
    r = pd.Series([-0.05, -0.04, 0.01, 0.02] * 25)
    assert M.cvar(r, 0.25) == pytest.approx(0.05)


def test_psr_properties():
    rng = np.random.default_rng(0)
    good = rng.normal(0.001, 0.01, 2000)
    null = rng.normal(0.0, 0.01, 2000)
    assert OF.probabilistic_sharpe_ratio(good) > 0.99
    assert 0.02 < OF.probabilistic_sharpe_ratio(null) < 0.98
    # PSR is monotone decreasing in the benchmark SR
    assert OF.probabilistic_sharpe_ratio(good, 0.05) < OF.probabilistic_sharpe_ratio(good, 0.0)


def test_expected_max_sharpe_grows_with_trials():
    v = 0.01**2
    e = [OF.expected_max_sharpe(n, v) for n in (2, 10, 100, 1000)]
    assert all(a < b for a, b in zip(e, e[1:]))
    assert OF.expected_max_sharpe(1, v) == 0.0


def test_dsr_penalises_selection_from_many_null_trials():
    """Pick the best of 200 pure-noise strategies: PSR looks impressive, DSR does not."""
    rng = np.random.default_rng(1)
    trials = rng.normal(0, 0.01, (750, 200))
    srs = trials.mean(0) / trials.std(0, ddof=1)
    best = trials[:, int(np.argmax(srs))]
    psr = OF.probabilistic_sharpe_ratio(best)
    dsr = OF.deflated_sharpe_ratio(best, 200, float(np.var(srs, ddof=1)))
    assert psr > 0.95
    assert dsr < 0.6


def test_bootstrap_ci_contains_point_and_detects_skill():
    rng = np.random.default_rng(2)
    r = rng.normal(0.0015, 0.01, 1500)
    pt, lo, hi = OF.stationary_bootstrap_ci(r, n_boot=500, seed=0)
    assert lo < pt < hi and lo > 0
    pt0, lo0, hi0 = OF.stationary_bootstrap_ci(rng.normal(0, 0.01, 500), n_boot=500, seed=0)
    assert lo0 < 0 < hi0


def test_pbo_high_for_noise_low_for_real_edge():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 0.01, (1000, 30))
    assert OF.pbo_cscv(noise, 10)["pbo"] > 0.25
    edge = noise.copy()
    edge[:, 7] += 0.004  # one configuration has a persistent, real edge
    assert OF.pbo_cscv(edge, 10)["pbo"] < 0.05


def test_multiple_testing_adjustments():
    p = [0.001, 0.01, 0.03, 0.04, 0.5]
    h = OF.holm(p)
    assert h.tolist() == pytest.approx([0.005, 0.04, 0.09, 0.09, 0.5])
    bh = OF.benjamini_hochberg(p)
    assert bh.tolist() == pytest.approx([0.005, 0.025, 0.05, 0.05, 0.5])
    assert all(bh <= h + 1e-12)


def test_stcg_estimate_by_financial_year():
    t = pd.DataFrame({"exit_date": pd.to_datetime(["2024-05-01", "2025-02-01", "2025-05-01"]),
                      "pnl": [100.0, -30.0, -50.0]})
    tax = M.estimate_stcg_tax(t, 0.2)
    assert tax.loc[2024] == pytest.approx(14.0) and tax.loc[2025] == 0.0
    assert math.isfinite(tax.sum())
