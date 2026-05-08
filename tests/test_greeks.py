"""Tests for v3/Ekubo Greeks. Anchor points from Lambert (2021) closed forms."""
import math
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.compute.greeks import (
    Position, position_amounts, position_value, delta, gamma,
    impermanent_loss, liquidity_from_value,
)


def test_position_validation():
    with pytest.raises(ValueError):
        Position(L=100, p_a=2000, p_b=1000)
    with pytest.raises(ValueError):
        Position(L=-1, p_a=1, p_b=2)
    with pytest.raises(ValueError):
        Position(L=100, p_a=0, p_b=2)


def test_below_range_is_all_token0():
    pos = Position(L=100, p_a=1500, p_b=2500)
    x, y = position_amounts(pos, p=1000)
    assert y == 0.0
    expected_x = 100 * (1 / math.sqrt(1500) - 1 / math.sqrt(2500))
    assert x == pytest.approx(expected_x, rel=1e-12)


def test_above_range_is_all_token1():
    pos = Position(L=100, p_a=1500, p_b=2500)
    x, y = position_amounts(pos, p=3000)
    assert x == 0.0
    expected_y = 100 * (math.sqrt(2500) - math.sqrt(1500))
    assert y == pytest.approx(expected_y, rel=1e-12)


def test_in_range_amounts_match_lambert():
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    x, y = position_amounts(pos, p)
    assert x == pytest.approx(100 * (1 / math.sqrt(2000) - 1 / math.sqrt(2500)), rel=1e-12)
    assert y == pytest.approx(100 * (math.sqrt(2000) - math.sqrt(1500)), rel=1e-12)


def test_lamberts_identity_delta_equals_x():
    """∂V/∂p = x(p) — Lambert's identity. Verified numerically."""
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    h = 0.01
    v_plus = position_value(pos, p + h)
    v_minus = position_value(pos, p - h)
    numerical_delta = (v_plus - v_minus) / (2 * h)
    analytical_delta = delta(pos, p)
    assert numerical_delta == pytest.approx(analytical_delta, rel=1e-4)


def test_gamma_negative_in_range():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert gamma(pos, 2000) < 0
    assert gamma(pos, 1000) == 0
    assert gamma(pos, 3000) == 0


def test_gamma_matches_second_derivative():
    pos = Position(L=100, p_a=1500, p_b=2500)
    p = 2000
    h = 0.5
    v_plus = position_value(pos, p + h)
    v_zero = position_value(pos, p)
    v_minus = position_value(pos, p - h)
    numerical_gamma = (v_plus - 2 * v_zero + v_minus) / (h ** 2)
    analytical_gamma = gamma(pos, p)
    assert numerical_gamma == pytest.approx(analytical_gamma, rel=1e-3)


def test_impermanent_loss_zero_at_entry():
    pos = Position(L=100, p_a=1500, p_b=2500)
    assert impermanent_loss(pos, p_t=2000, p_0=2000) == pytest.approx(0.0, abs=1e-12)


def test_impermanent_loss_negative_when_price_moves():
    pos = Position(L=100, p_a=1500, p_b=2500)
    # Price moves up; LP underperforms HODL.
    il_up = impermanent_loss(pos, p_t=2400, p_0=2000)
    il_down = impermanent_loss(pos, p_t=1600, p_0=2000)
    assert il_up < 0
    assert il_down < 0


def test_liquidity_from_value_round_trip():
    p, p_a, p_b = 2000, 1500, 2500
    target_value = 10000.0  # USDC
    L = liquidity_from_value(target_value, p, p_a, p_b)
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    assert position_value(pos, p) == pytest.approx(target_value, rel=1e-10)
