"""Tests for the P4 risk engine."""
import math
import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.risk import (
    historical_var, gaussian_var, cornish_fisher_var,
    cvar_expected_shortfall, var_decomposition,
    PriceShockUp, PriceShockDown, PegBreak, VolSpike, LiquidityCrisis,
    run_stress_book,
    sample_covariance, ledoit_wolf_shrinkage,
    correlation_from_covariance, factor_decomposition,
    attribute_pnl, factor_attribution,
)
from lvr_lab.domain.position import Position


# ---------- VaR ----------
def test_historical_var_returns_positive():
    rng = np.random.default_rng(0)
    rets = rng.normal(-0.001, 0.02, size=1000)
    v = historical_var(rets, alpha=0.05, notional=100_000)
    assert v > 0


def test_gaussian_var_matches_analytical():
    """For pure normal returns, gaussian_var should match the closed form."""
    rng = np.random.default_rng(0)
    sigma = 0.02
    rets = rng.normal(0.0, sigma, size=10_000)
    v = gaussian_var(rets, alpha=0.05, notional=1.0)
    # Expected: ~1.645 σ
    expected = 1.645 * sigma
    assert abs(v - expected) < 0.005


def test_cornish_fisher_handles_skewed_data():
    rng = np.random.default_rng(0)
    # Generate left-skewed returns
    base = rng.normal(0.0, 0.02, 1000)
    skewed = base - 0.001 * (base < 0)  # extra weight on negative returns
    cf = cornish_fisher_var(skewed)
    g = gaussian_var(skewed)
    # Both should be finite and positive
    assert math.isfinite(cf) and cf > 0
    assert math.isfinite(g)


def test_cvar_geq_var():
    """CVaR is the expected loss conditional on exceeding VaR — always ≥ VaR."""
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0, 0.02, 1000)
    v = historical_var(rets, alpha=0.05)
    cvar = cvar_expected_shortfall(rets, alpha=0.05)
    assert cvar >= v


def test_var_decomposition_returns_full_dict():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0, 0.02, 1000)
    out = var_decomposition(rets, notional=100_000, alphas=(0.01, 0.05))
    assert "alpha_0.01" in out and "alpha_0.05" in out
    for a in out:
        for method in ("historical", "gaussian", "cornish_fisher", "cvar"):
            assert method in out[a]


# ---------- Stress ----------
def test_price_shock_up():
    pos = Position(L=100, p_a=1500, p_b=2500)
    r = PriceShockUp(magnitude=0.5).apply(pos, p_pre=2000)
    assert r.scenario_name.startswith("PriceShockUp")
    assert r.pre_value > 0
    assert r.post_value > 0


def test_price_shock_down():
    pos = Position(L=100, p_a=1500, p_b=2500)
    r = PriceShockDown(magnitude=0.5).apply(pos, p_pre=2000)
    assert r.delta_value < 0   # underperforms HODL when price drops


def test_vol_spike_produces_negative_pnl():
    pos = Position(L=100, p_a=1500, p_b=2500)
    r = VolSpike(target_sigma=2.0, duration_days=7).apply(pos, p_pre=2000, sigma_pre=0.4)
    assert r.delta_value < 0
    assert "σ→200%" in r.scenario_name


def test_liquidity_crisis_negative():
    pos = Position(L=100, p_a=1500, p_b=2500)
    r = LiquidityCrisis().apply(pos, p_pre=2000)
    assert r.delta_value < 0


def test_run_stress_book_returns_all_scenarios():
    pos = Position(L=100, p_a=1500, p_b=2500)
    book = run_stress_book(pos, p=2000, sigma=0.4)
    assert len(book) >= 5
    for name, result in book.items():
        assert result.pre_value > 0


# ---------- Correlation ----------
def test_ledoit_wolf_returns_psd_matrix():
    rng = np.random.default_rng(0)
    n, p = 50, 5
    X = rng.normal(size=(n, p))
    cov = ledoit_wolf_shrinkage(X)
    assert cov.shape == (p, p)
    eigvals = np.linalg.eigvalsh(cov)
    assert (eigvals > -1e-10).all(), "covariance must be PSD"


def test_ledoit_wolf_diagonal_close_to_sample_var():
    rng = np.random.default_rng(0)
    X = rng.normal(scale=2.0, size=(100, 4))
    cov = ledoit_wolf_shrinkage(X)
    diag_lw = np.diag(cov)
    diag_sample = X.var(axis=0, ddof=1)
    # Shrinkage pulls diagonal toward the mean variance, but they should be reasonably close
    np.testing.assert_allclose(diag_lw, diag_sample, rtol=0.2)


def test_factor_decomposition_explains_variance():
    rng = np.random.default_rng(0)
    n, p = 100, 6
    common_factor = rng.normal(size=n)
    X = np.outer(common_factor, np.ones(p)) + 0.1 * rng.normal(size=(n, p))
    out = factor_decomposition(X, k_factors=2)
    # First factor should explain most of the variance (common factor dominates)
    assert out["explained_variance_ratio"][0] > 0.5


# ---------- Attribution ----------
def test_attribute_pnl_residual_close_to_zero_when_components_sum():
    """When all components are provided correctly, residual should be 0."""
    delta_v_lp = 100.0
    fees = 50.0
    lvr = 30.0
    hedge_pnl = -10.0
    total = delta_v_lp + fees - lvr + hedge_pnl   # = 110
    report = attribute_pnl(
        total_pnl=total,
        delta_v_lp=delta_v_lp,
        fees=fees,
        lvr=lvr,
        hedge_pnl=hedge_pnl,
    )
    assert abs(report.residual) < 1e-9
    # Total pct sums correctly
    assert report.total_pnl == pytest.approx(110.0)


def test_factor_attribution_runs():
    rng = np.random.default_rng(0)
    R = rng.normal(size=(50, 4))
    L = rng.normal(size=(4, 2))
    out = factor_attribution(R, L)
    assert "explained_T_x_N" in out
    assert "idiosyncratic_T_x_N" in out
    assert out["explained_T_x_N"].shape == (50, 4)
