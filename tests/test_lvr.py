"""Tests for instantaneous LVR formulas."""
import math
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.compute.greeks import Position
from lvr_lab.compute.lvr import (
    marginal_liquidity, lvr_rate_in_range, lvr_integrated, lvr_rate_proxy_from_tvl,
)


def test_marginal_liquidity_in_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    expected = 100 / (2 * math.sqrt(2000))
    assert marginal_liquidity(pos, p) == pytest.approx(expected, rel=1e-12)


def test_marginal_liquidity_out_of_range_zero():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert marginal_liquidity(pos, 1000) == 0.0
    assert marginal_liquidity(pos, 3000) == 0.0


def test_lvr_rate_proportional_to_sigma_squared():
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    r1 = lvr_rate_in_range(pos, p, sigma=0.5)
    r2 = lvr_rate_in_range(pos, p, sigma=1.0)
    # σ doubles → LVR rate quadruples.
    assert r2 == pytest.approx(4 * r1, rel=1e-12)


def test_lvr_rate_milionis_closed_form():
    """LVR_rate = σ² · L · √p / 4  (Milionis et al. 2022 in-range CL)."""
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    sigma = 0.6
    expected = (sigma ** 2) * 100 * math.sqrt(2000) / 4.0
    assert lvr_rate_in_range(pos, p, sigma) == pytest.approx(expected, rel=1e-12)


def test_lvr_integrated_constant_price_path():
    pos = Position(L=100, p_a=1500, p_b=2500)
    sigma = 0.5
    # constant price for 1 second: LVR = rate × 1
    rate = lvr_rate_in_range(pos, 2000, sigma)
    integrated = lvr_integrated(pos, [(0.0, 2000.0), (1.0, 2000.0)], sigma)
    assert integrated == pytest.approx(rate * 1.0, rel=1e-12)


def test_lvr_integrated_handles_unsorted_times():
    pos = Position(L=100, p_a=1500, p_b=2500)
    sigma = 0.5
    a = lvr_integrated(pos, [(0.0, 2000.0), (1.0, 2000.0), (2.0, 2000.0)], sigma)
    b = lvr_integrated(pos, [(2.0, 2000.0), (0.0, 2000.0), (1.0, 2000.0)], sigma)
    assert a == pytest.approx(b, rel=1e-12)


def test_lvr_proxy_from_tvl_units():
    """σ_fee derived from the TVL proxy must satisfy σ_fee = 2·sqrt(fee_yield)."""
    from lvr_lab.compute.sigma_fee import sigma_fee_from_tvl_proxy, SECONDS_PER_YEAR
    tvl = 1_000_000.0
    fees_one_week = 1000.0  # 7 days
    dt = 7 * 24 * 3600
    sigma_fee = sigma_fee_from_tvl_proxy(fees_one_week, tvl, dt)
    fee_yield = (fees_one_week * SECONDS_PER_YEAR / dt) / tvl
    expected = 2.0 * math.sqrt(fee_yield)
    assert sigma_fee == pytest.approx(expected, rel=1e-12)
