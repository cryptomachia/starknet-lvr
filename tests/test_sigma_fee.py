"""Tests for σ_fee solver — closed form and Brent root-finder agreement."""
import math
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.compute.sigma_fee import (
    sigma_fee_closed_form, sigma_fee_solve, sigma_fee_from_tvl_proxy, SECONDS_PER_YEAR,
)


def test_closed_form_recovers_input_sigma():
    """Generate fees from a known σ via the LVR formula, then recover σ."""
    L = 100.0
    p = 2000.0
    sigma_true = 0.6  # annualized
    dt = 30 * 24 * 3600.0  # 30 days in seconds
    # expected fees (= expected LVR) over dt from σ_true
    fees = (sigma_true ** 2) * L * math.sqrt(p) * dt / 4.0
    sigma_recovered = sigma_fee_closed_form(fees, L, p, dt, annualized=True)
    assert sigma_recovered == pytest.approx(sigma_true, rel=1e-12)


def test_closed_form_handles_zero_inputs():
    assert math.isnan(sigma_fee_closed_form(0, 100, 2000, 1))
    assert math.isnan(sigma_fee_closed_form(100, 0, 2000, 1))
    assert math.isnan(sigma_fee_closed_form(100, 100, 0, 1))
    assert math.isnan(sigma_fee_closed_form(100, 100, 2000, 0))


def test_general_solver_agrees_with_closed_form():
    L, p = 100.0, 2000.0
    dt = 30 * 24 * 3600.0
    sigma_true = 0.5
    fees = (sigma_true ** 2) * L * math.sqrt(p) * dt / 4.0

    def expected_fees(_sigma):
        return fees  # σ-independent (constant fee tier)

    def expected_lvr(sigma):
        return (sigma ** 2) * L * math.sqrt(p) * dt / 4.0

    sigma_solved = sigma_fee_solve(expected_fees, expected_lvr, sigma_low=1e-4, sigma_high=5.0)
    assert sigma_solved is not None
    assert sigma_solved == pytest.approx(sigma_true, rel=1e-6)


def test_no_root_returns_none():
    """If LVR > fees on the entire bracket, solver returns None."""
    sigma_solved = sigma_fee_solve(
        expected_fees=lambda s: 1.0,
        expected_lvr=lambda s: 100.0 * s ** 2,
        sigma_low=10, sigma_high=20,
    )
    assert sigma_solved is None


def test_tvl_proxy_consistent_with_fee_yield_identity():
    """σ_fee from TVL proxy = 2·√(annualized_fee_yield)."""
    tvl, fees, dt = 1_000_000.0, 500.0, 7 * 24 * 3600.0
    sigma_fee = sigma_fee_from_tvl_proxy(fees, tvl, dt)
    fee_yield_ann = (fees * SECONDS_PER_YEAR / dt) / tvl
    assert sigma_fee == pytest.approx(2.0 * math.sqrt(fee_yield_ann), rel=1e-12)
