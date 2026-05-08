"""Tests for the inferential-stats helpers."""
import numpy as np
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.analysis.hypothesis_tests import (
    newey_west_se, fama_macbeth, iv_2sls, benjamini_hochberg,
)


def test_newey_west_recovers_ols_beta_with_extra_se():
    rng = np.random.default_rng(0)
    n = 500
    x = np.column_stack([np.ones(n), rng.normal(size=n)])
    beta_true = np.array([1.0, 2.0])
    eps = rng.normal(size=n)
    y = x @ beta_true + eps
    beta, se = newey_west_se(y, x, lag=4)
    assert beta == pytest.approx(beta_true, abs=0.15)
    assert (se > 0).all()


def test_fama_macbeth_basic():
    rng = np.random.default_rng(1)
    T, N, K = 50, 30, 3
    beta_true = np.array([0.5, 1.0, -0.3])
    x_panel = rng.normal(size=(T, N, K))
    y_panel = (x_panel @ beta_true) + rng.normal(scale=0.1, size=(T, N))
    res = fama_macbeth(y_panel, x_panel)
    assert res.coef == pytest.approx(beta_true, abs=0.1)
    assert (res.se > 0).all()


def test_iv_2sls_identification():
    rng = np.random.default_rng(2)
    n = 500
    z = rng.normal(size=(n, 1))
    x_endog = z + 0.5 * rng.normal(size=(n, 1))
    x_exog = np.ones((n, 1))
    y = 2.0 * x_endog[:, 0] + 0.3 + rng.normal(scale=0.5, size=n)
    beta, se = iv_2sls(y, x_endog, x_exog, z)
    assert beta[0] == pytest.approx(2.0, abs=0.2)


def test_bh_no_rejections_under_null():
    pvals = [0.4, 0.6, 0.8]
    out = benjamini_hochberg(pvals, alpha=0.05)
    assert out == [False, False, False]


def test_bh_some_rejections_when_signal_strong():
    pvals = [0.001, 0.002, 0.6]
    out = benjamini_hochberg(pvals, alpha=0.05)
    assert out[0] is True and out[1] is True and out[2] is False
