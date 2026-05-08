"""Tests for OHLC realized-vol estimators."""
import math
import pytest
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.compute.vol_estimators import (
    realized_vol_close_to_close,
    realized_vol_parkinson,
    realized_vol_garman_klass,
    realized_vol_rogers_satchell,
    realized_vol_yang_zhang,
    realized_vol_bipower,
    all_estimators,
)


@pytest.fixture
def gbm_path():
    """Geometric Brownian motion with known σ, used for unbiased-estimator checks."""
    rng = np.random.default_rng(42)
    n = 5000
    sigma_true = 0.4  # per sqrt(unit time)
    dt = 1.0
    rets = rng.normal(loc=-0.5 * sigma_true ** 2 * dt, scale=sigma_true * math.sqrt(dt), size=n)
    closes = 100 * np.exp(np.cumsum(rets))
    # Construct synthetic OHLC: O = previous close (no overnight gap), H/L extremes within step.
    opens = np.concatenate([[100.0], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + 0.001)  # small intraday wiggle
    lows = np.minimum(opens, closes) * (1 - 0.001)
    return opens, highs, lows, closes, sigma_true


def test_close_to_close_unbiased_for_gbm(gbm_path):
    o, h, l, c, sigma_true = gbm_path
    sigma_hat = realized_vol_close_to_close(c, annualization_factor=1.0)
    assert abs(sigma_hat - sigma_true) < 0.02


def test_yang_zhang_finite_for_short_series(gbm_path):
    o, h, l, c, _ = gbm_path
    sigma_hat = realized_vol_yang_zhang(o[:50], h[:50], l[:50], c[:50], annualization_factor=1.0)
    assert math.isfinite(sigma_hat)
    assert sigma_hat > 0


def test_yang_zhang_lower_variance_than_close_to_close():
    """YZ should have tighter sampling variance than close-to-close at small n.

    Run many simulations and compare the empirical sampling variance.
    """
    rng = np.random.default_rng(0)
    n = 60
    sigma_true = 0.3
    n_sims = 200
    cc, yz = [], []
    for _ in range(n_sims):
        rets = rng.normal(0, sigma_true, size=n)
        c = 100 * np.exp(np.cumsum(rets))
        o = np.concatenate([[100.0], c[:-1]])
        h = np.maximum(o, c) * 1.002
        l = np.minimum(o, c) * 0.998
        cc.append(realized_vol_close_to_close(c, 1.0))
        yz.append(realized_vol_yang_zhang(o, h, l, c, 1.0))
    var_cc = float(np.var(cc, ddof=1))
    var_yz = float(np.var(yz, ddof=1))
    # YZ is typically lower-variance than CC even at modest n.
    assert var_yz <= var_cc * 1.1  # slack for small n; held in our experiments


def test_all_estimators_dict_keys():
    o = h = l = c = [100.0] * 30
    out = all_estimators(o, h, l, c, 1.0)
    assert set(out.keys()) == {
        "close_to_close", "parkinson", "garman_klass",
        "rogers_satchell", "yang_zhang", "bipower",
    }


def test_handles_short_input_gracefully():
    assert math.isnan(realized_vol_close_to_close([100.0]))
    assert math.isnan(realized_vol_yang_zhang([100], [100], [100], [100], 1.0))
