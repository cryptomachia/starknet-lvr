"""Tests for the P0 domain layer."""
import math
import pytest
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.domain import (
    Position, Portfolio, PortfolioGreeks,
    UniformBandProfile, ConcentratedActiveProfile,
    OnchainReconstructedProfile, PiecewiseProfile,
    short_string_to_felt, felt_to_short_string,
    tick_to_price, price_to_tick, nearest_tick,
    bps_to_fraction, fraction_to_bps,
    PoolKey, FeeTier, AmmFamily,
    SwapEvent, PositionOpenedEvent, OracleUpdateEvent,
    in_range_status, PositionState,
)
from lvr_lab.domain.profile import PiecewiseProfile  # noqa
from lvr_lab.compute.greeks import (
    speed, lvr_vega, dollar_gamma, value_curve, delta_curve, gamma_curve,
    portfolio_value_curve, time_in_range, expected_capital_efficiency,
    il_term_structure,
)


# ---------- Types & encoding ----------
def test_short_string_roundtrip():
    for s in ["ETH/USD", "BTC", "STRK/USD"]:
        assert felt_to_short_string(short_string_to_felt(s)) == s


def test_short_string_too_long():
    with pytest.raises(ValueError):
        short_string_to_felt("x" * 32)


def test_tick_price_roundtrip():
    for p in [100.0, 2000.0, 50000.0]:
        t = price_to_tick(p)
        # tick_to_price floor approximation; ensure within 1 tick
        assert abs(tick_to_price(t) - p) / p < 1e-3


def test_bps_helpers():
    assert bps_to_fraction(5) == pytest.approx(0.0005)
    assert fraction_to_bps(0.001) == pytest.approx(10)


# ---------- Position metadata + serialization ----------
def test_position_to_from_dict_roundtrip():
    pos = Position(L=100, p_a=1500, p_b=2500, owner=42, nft_id=7,
                   opened_block=1000, opened_price=2000)
    d = pos.to_dict()
    pos2 = Position.from_dict(d)
    assert pos == pos2


def test_position_status_classifier():
    assert in_range_status(1000, 1500, 2500) == PositionState.BELOW_RANGE
    assert in_range_status(2000, 1500, 2500) == PositionState.IN_RANGE
    assert in_range_status(3000, 1500, 2500) == PositionState.ABOVE_RANGE


def test_position_width_pct():
    pos = Position(L=100, p_a=1900, p_b=2100)  # half-width ≈ 2.5% on √-scale
    assert 0.02 < pos.width_pct < 0.03


# ---------- Profiles ----------
def test_uniform_band_profile_builds_correctly():
    prof = UniformBandProfile(width_pct=0.05)
    pos = prof.build(capital_token1=10_000.0, current_price=2000.0)
    assert pos.in_range(2000.0)
    assert pos.opened_price == 2000.0
    # value at p_0 should equal the capital we put in
    from lvr_lab.compute.greeks import position_value
    assert position_value(pos, 2000.0) == pytest.approx(10_000.0, rel=1e-9)


def test_concentrated_active_tighter_than_uniform():
    capital, p = 10_000.0, 2000.0
    uni = UniformBandProfile(width_pct=0.05).build(capital, p)
    conc = ConcentratedActiveProfile(half_width_pct=0.005).build(capital, p)
    # Concentrated has higher L for the same capital
    assert conc.L > uni.L


def test_piecewise_profile_sums_to_capital():
    prof = PiecewiseProfile(
        ranges=[(0.95, 1.05), (0.90, 1.10), (0.80, 1.20)],
        weights=[0.5, 0.3, 0.2],
    )
    positions = prof.build_all(capital_token1=10_000.0, current_price=2000.0)
    from lvr_lab.compute.greeks import position_value
    total_value = sum(position_value(p, 2000.0) for p in positions)
    assert total_value == pytest.approx(10_000.0, rel=1e-9)


# ---------- Portfolio ----------
def test_portfolio_aggregate_greeks_linear():
    p1 = Position(L=100, p_a=1900, p_b=2100)
    p2 = Position(L=200, p_a=1500, p_b=2500)
    pf = Portfolio([p1, p2])
    g = pf.aggregate_greeks(2000.0)
    # delta should sum
    from lvr_lab.compute.greeks import delta as delta_fn
    expected_delta = delta_fn(p1, 2000.0) + delta_fn(p2, 2000.0)
    assert g.delta_token0 == pytest.approx(expected_delta, rel=1e-9)
    assert g.n_positions_in_range == 2


def test_portfolio_in_range_subset():
    p1 = Position(L=100, p_a=1900, p_b=2100)   # in range at 2000
    p2 = Position(L=200, p_a=2200, p_b=2400)   # below at 2000
    pf = Portfolio([p1, p2])
    sub = pf.in_range_at(2000.0)
    assert len(sub) == 1


# ---------- New Greek functions ----------
def test_speed_zero_outside_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert speed(pos, 1000) == 0.0
    assert speed(pos, 3000) == 0.0


def test_speed_positive_in_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert speed(pos, 2000) > 0


def test_lvr_vega_proportional_to_sigma():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert lvr_vega(pos, 2000, sigma=0.5) > 0
    # Linear in σ
    v1 = lvr_vega(pos, 2000, sigma=0.3)
    v2 = lvr_vega(pos, 2000, sigma=0.6)
    assert v2 == pytest.approx(2 * v1, rel=1e-9)


def test_dollar_gamma_scaling():
    pos = Position(L=100, p_a=1500, p_b=2500)
    g = dollar_gamma(pos, 2000.0)
    assert g > 0
    assert dollar_gamma(pos, 1000) == 0  # outside range


# ---------- Vectorized curves ----------
def test_value_curve_matches_scalar():
    pos = Position(L=100, p_a=1500, p_b=2500)
    prices = np.array([1000.0, 1500.0, 1800.0, 2000.0, 2200.0, 2500.0, 3000.0])
    curve = value_curve(pos, prices)
    from lvr_lab.compute.greeks import position_value
    for i, p in enumerate(prices):
        # In-range: exact match. At boundaries: tolerate small diff (we treat p == p_a as below).
        if pos.p_a < p < pos.p_b:
            assert curve[i] == pytest.approx(position_value(pos, p), rel=1e-9)


def test_delta_curve_matches_scalar():
    pos = Position(L=100, p_a=1500, p_b=2500)
    prices = np.linspace(1700, 2300, 13)
    curve = delta_curve(pos, prices)
    from lvr_lab.compute.greeks import delta as delta_fn
    for i, p in enumerate(prices):
        assert curve[i] == pytest.approx(delta_fn(pos, p), rel=1e-9)


def test_portfolio_value_curve_is_linear_sum():
    pos1 = Position(L=100, p_a=1900, p_b=2100)
    pos2 = Position(L=200, p_a=1800, p_b=2200)
    pf = Portfolio([pos1, pos2])
    prices = np.linspace(1850, 2150, 7)
    aggregate = portfolio_value_curve(pf, prices)
    individual = value_curve(pos1, prices) + value_curve(pos2, prices)
    np.testing.assert_allclose(aggregate, individual, rtol=1e-9)


def test_time_in_range_metric():
    pos = Position(L=100, p_a=1900, p_b=2100)
    prices = np.array([1850, 1950, 2000, 2050, 2150])
    # Three of five in range
    assert time_in_range(prices, pos) == pytest.approx(3 / 5)


def test_expected_capital_efficiency_concentrated_higher():
    p_ref = 2000.0
    wide = Position(L=100, p_a=p_ref * 0.5, p_b=p_ref * 1.5)
    narrow = Position(L=100, p_a=p_ref * 0.95, p_b=p_ref * 1.05)
    # Same L; narrow → less capital required → higher efficiency ratio
    assert expected_capital_efficiency(narrow, p_ref) > expected_capital_efficiency(wide, p_ref)


def test_il_term_structure_basic():
    pos = Position(L=100, p_a=1500, p_b=2500, opened_price=2000)
    p_0 = 2000
    path = [(0, 2000), (86400, 2050), (7 * 86400, 2100), (30 * 86400, 2200)]
    out = il_term_structure(pos, path, p_0)
    assert "1d" in out and "7d" in out and "30d" in out
    assert out["1d"] < 0  # IL is negative when price moves


# ---------- PoolKey + Pool ----------
def test_poolkey_normalized_orders_tokens():
    k = PoolKey(token0=2, token1=1, fee_bps=5, tick_spacing=1)
    norm = k.normalized()
    assert norm.token0 < norm.token1


def test_fee_tier_dynamic_flag():
    static = FeeTier(base_bps=5)
    dynamic = FeeTier(base_bps=5, dynamic_extension=42, max_bps=100)
    assert not static.is_dynamic()
    assert dynamic.is_dynamic()


# ---------- Domain events ----------
def test_swap_event_carries_required_fields():
    from lvr_lab.domain import Swap, SwapDirection
    s = Swap(
        pool_id="0xpool", direction=SwapDirection.TOKEN0_TO_TOKEN1,
        amount0_in_wei=1000, amount1_in_wei=0, fee_amount_wei=5,
        fee_token_is_0=True, pool_price_pre=2000.0, pool_price_post=1995.0,
        pool_tick_pre=100, pool_tick_post=99,
        block_number=12345, block_timestamp=1.0, tx_hash="0xabc", log_index=0,
    )
    ev = SwapEvent(block_number=12345, block_timestamp=1.0, tx_hash="0xabc",
                   log_index=0, swap=s)
    assert ev.event_type == "swap"
    assert ev.swap.pool_id == "0xpool"


def test_oracle_update_event():
    ev = OracleUpdateEvent(
        block_number=100, block_timestamp=1.0, tx_hash="0xtx",
        oracle=0x2a85, pair_id=short_string_to_felt("ETH/USD"),
        price=2288.30, decimals=8, n_sources_aggregated=14,
    )
    assert ev.event_type == "oracle_update"
    assert ev.price == 2288.30
