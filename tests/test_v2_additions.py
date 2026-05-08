"""Tests for the v2 additions: selectors, optimal hedge, Squeeth, cluster bootstrap, cointegration."""
import math
import pytest
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.compute.selectors import (
    starknet_keccak, selector_hex, short_string_to_felt, short_string_to_felt_hex,
)
from lvr_lab.compute.optimal_hedge import (
    HedgeParams, optimal_hedge_ratio, hedge_size_token0, funding_aware_hedge_ratio,
)
from lvr_lab.compute.squeeth import (
    squeeth_size_to_flatten_gamma, residual_delta_after_squeeth,
    squeeth_pnl_step, SQUEETH_GAMMA,
)
from lvr_lab.compute.greeks import Position, gamma
from lvr_lab.compute.bootstrap import (
    cluster_bootstrap_ci, sector_for_pool, block_bootstrap_ci,
)
from lvr_lab.analysis.cointegration import engle_granger


# ---------- Selectors ----------
def test_starknet_keccak_is_deterministic():
    assert starknet_keccak("get_data_median") == starknet_keccak("get_data_median")


def test_starknet_keccak_returns_250_bits():
    sel = starknet_keccak("transfer")
    assert sel < (1 << 250)


def test_short_string_encoding():
    assert short_string_to_felt("ETH/USD") == int.from_bytes(b"ETH/USD", "big")
    assert short_string_to_felt_hex("ETH/USD") == "0x4554482f555344"


def test_short_string_too_long_raises():
    with pytest.raises(ValueError):
        short_string_to_felt("x" * 32)


# ---------- Optimal hedge ----------
def test_optimal_hedge_zero_basis_yields_full_hedge():
    """σ_basis=0 ⇒ no penalty for hedging ⇒ ρ → 1."""
    p = HedgeParams(sigma=0.5, sigma_basis=0.0, delay_years=1e-6,
                    risk_aversion=1.0, txn_cost_coef=0.0, horizon_years=1.0)
    assert optimal_hedge_ratio(p) == pytest.approx(1.0, abs=1e-9)


def test_optimal_hedge_high_basis_yields_low_hedge():
    """High σ_basis × delay should pull ρ toward 0."""
    p_lo_basis = HedgeParams(sigma=0.5, sigma_basis=0.01, delay_years=30 / 31536000,
                              risk_aversion=1.0, txn_cost_coef=0.0, horizon_years=1 / 365)
    p_hi_basis = HedgeParams(sigma=0.5, sigma_basis=2.0, delay_years=30 / 31536000,
                              risk_aversion=1.0, txn_cost_coef=0.0, horizon_years=1 / 365)
    assert optimal_hedge_ratio(p_hi_basis) < optimal_hedge_ratio(p_lo_basis)


def test_hedge_size_signs():
    p = HedgeParams(sigma=0.5, sigma_basis=0.0, delay_years=0,
                    risk_aversion=1.0, txn_cost_coef=0.0, horizon_years=1.0)
    assert hedge_size_token0(10.0, p) < 0   # positive Δ ⇒ short
    assert hedge_size_token0(-10.0, p) > 0  # negative Δ ⇒ long


def test_funding_aware_hedge_drops_with_funding_cost():
    p = HedgeParams(sigma=0.5, sigma_basis=0.01, delay_years=0,
                    risk_aversion=1.0, txn_cost_coef=0.0, horizon_years=1.0)
    rho_no_fund = funding_aware_hedge_ratio(p, funding_rate=0.0)
    rho_costly = funding_aware_hedge_ratio(p, funding_rate=0.20)  # 20% APR
    assert rho_costly < rho_no_fund


# ---------- Squeeth ----------
def test_squeeth_zero_outside_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert squeeth_size_to_flatten_gamma(pos, 1000) == 0.0
    assert squeeth_size_to_flatten_gamma(pos, 3000) == 0.0


def test_squeeth_size_offsets_lp_gamma_in_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    n_sqth = squeeth_size_to_flatten_gamma(pos, p)
    g_lp = gamma(pos, p)
    # n_sqth × Γ_squeeth + Γ_LP ≈ 0
    net_gamma = n_sqth * SQUEETH_GAMMA + g_lp
    assert abs(net_gamma) < 1e-9


def test_squeeth_pnl_zero_when_price_unchanged():
    pnl, fund = squeeth_pnl_step(prev_n_sqth=0.05, prev_p=2000, p=2000,
                                 funding_apr=0.10, dt_seconds=86400)
    assert pnl == 0.0
    assert fund > 0  # funding still accrues


# ---------- Cluster bootstrap ----------
def test_sector_for_pool_classification():
    assert sector_for_pool("USDC", "USDT") == "stable"
    assert sector_for_pool("USDC", "ETH") == "eth"
    assert sector_for_pool("USDC", "WBTC") == "btc"
    assert sector_for_pool("USDC", "STRK") == "strk"
    assert sector_for_pool("WBTC", "STRK") == "btc"  # btc takes precedence by order
    assert sector_for_pool("xSTRK", "STRK") == "strk"


def test_cluster_bootstrap_returns_finite_ci():
    rng = np.random.default_rng(0)
    vals = list(rng.normal(size=20))
    clusters = [i // 5 for i in range(20)]   # 4 clusters of 5
    pt, lo, hi = cluster_bootstrap_ci(vals, clusters, statistic=np.mean,
                                       n_resamples=500, seed=0)
    assert lo <= pt <= hi
    assert math.isfinite(pt) and math.isfinite(lo) and math.isfinite(hi)


def test_cluster_bootstrap_falls_back_with_one_cluster():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    clusters = ["A"] * 5
    pt, lo, hi = cluster_bootstrap_ci(vals, clusters, statistic=np.mean,
                                       n_resamples=200, seed=0)
    assert lo <= pt <= hi


# ---------- Cointegration ----------
def test_cointegration_detects_pegged_pair():
    rng = np.random.default_rng(0)
    n = 500
    common = np.cumsum(rng.normal(scale=0.01, size=n))
    x = 100 * np.exp(common)
    y = x * np.exp(rng.normal(scale=0.001, size=n))  # tightly pegged to x
    res = engle_granger(y, x)
    assert res.cointegrated is True
    assert 0.9 < res.beta < 1.1


def test_cointegration_rejects_unrelated_pair():
    rng = np.random.default_rng(1)
    n = 500
    x = np.exp(np.cumsum(rng.normal(scale=0.01, size=n)))
    y = np.exp(np.cumsum(rng.normal(scale=0.01, size=n)))  # independent
    res = engle_granger(y, x)
    # With 500 obs and independent random walks, ADF should not reject
    assert res.p_value > 0.05
