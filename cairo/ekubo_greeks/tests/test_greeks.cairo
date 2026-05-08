//! Cairo tests for the ekubo-greeks library.
//!
//! Anchor points cross-validated against the Python reference oracle in
//! `tests/reference_oracle.py`. Run with `scarb cairo-test`.

use ekubo_greeks::position::{Position, position_amounts, position_value};
use ekubo_greeks::greeks::{delta_token0, gamma_abs, dollar_gamma, lvr_vega};
use ekubo_greeks::lvr::{lvr_rate_in_range_token1, marginal_liquidity, sigma_fee};
use ekubo_greeks::il::{
    impermanent_loss_vs_hodl_abs,
    impermanent_loss_vs_passive_abs,
};

const ONE_E18: u256 = 1_000_000_000_000_000_000_u256;

fn _pos() -> Position {
    Position {
        L: 100_u256 * ONE_E18,
        p_a: 1500_u256 * ONE_E18,
        p_b: 2500_u256 * ONE_E18,
    }
}

#[test]
fn test_below_range_is_all_token0() {
    let pos = _pos();
    let amts = position_amounts(pos, 1000_u256 * ONE_E18);
    assert(amts.y_token1 == 0_u256, 'should be all token0');
    assert(amts.x_token0 > 0_u256, 'token0 must be positive');
}

#[test]
fn test_above_range_is_all_token1() {
    let pos = _pos();
    let amts = position_amounts(pos, 3000_u256 * ONE_E18);
    assert(amts.x_token0 == 0_u256, 'should be all token1');
    assert(amts.y_token1 > 0_u256, 'token1 must be positive');
}

#[test]
fn test_lambert_identity_delta_equals_x_in_range() {
    let pos = _pos();
    let p = 2000_u256 * ONE_E18;
    let amts = position_amounts(pos, p);
    let d = delta_token0(pos, p);
    assert(d == amts.x_token0, 'lambert identity violated');
}

#[test]
fn test_gamma_zero_outside_range() {
    let pos = _pos();
    assert(gamma_abs(pos, 1000_u256 * ONE_E18) == 0_u256, 'gamma below = 0');
    assert(gamma_abs(pos, 3000_u256 * ONE_E18) == 0_u256, 'gamma above = 0');
}

#[test]
fn test_gamma_positive_in_range() {
    let pos = _pos();
    let g = gamma_abs(pos, 2000_u256 * ONE_E18);
    assert(g > 0_u256, 'gamma must be > 0 in range');
}

#[test]
fn test_lvr_zero_outside_range() {
    let pos = _pos();
    let sigma = 600_000_000_000_000_000_u256;  // 0.6 in 18-dec FP
    assert(lvr_rate_in_range_token1(pos, 1000_u256 * ONE_E18, sigma) == 0_u256, 'lvr below = 0');
    assert(lvr_rate_in_range_token1(pos, 3000_u256 * ONE_E18, sigma) == 0_u256, 'lvr above = 0');
}

#[test]
fn test_lvr_positive_in_range() {
    let pos = _pos();
    let sigma = 600_000_000_000_000_000_u256;
    let r = lvr_rate_in_range_token1(pos, 2000_u256 * ONE_E18, sigma);
    assert(r > 0_u256, 'lvr in-range > 0');
}

#[test]
fn test_marginal_liquidity_zero_outside() {
    let pos = _pos();
    assert(marginal_liquidity(pos, 1000_u256 * ONE_E18) == 0_u256, 'ml below = 0');
    assert(marginal_liquidity(pos, 3000_u256 * ONE_E18) == 0_u256, 'ml above = 0');
}

#[test]
fn test_il_vs_hodl_zero_at_open_price() {
    let pos = _pos();
    let p_0 = 2000_u256 * ONE_E18;
    let il = impermanent_loss_vs_hodl_abs(pos, p_0, p_0);
    assert(il == 0_u256, 'il at open = 0');
}

#[test]
fn test_il_vs_passive_zero_at_open_price() {
    let pos = _pos();
    let p_0 = 2000_u256 * ONE_E18;
    let il = impermanent_loss_vs_passive_abs(pos, p_0, p_0);
    assert(il == 0_u256, 'il_passive at open = 0');
}

#[test]
fn test_lvr_vega_zero_outside_range() {
    let pos = _pos();
    let sigma = 500_000_000_000_000_000_u256;
    assert(lvr_vega(pos, 1000_u256 * ONE_E18, sigma) == 0_u256, 'vega below = 0');
    assert(lvr_vega(pos, 3000_u256 * ONE_E18, sigma) == 0_u256, 'vega above = 0');
}

#[test]
fn test_dollar_gamma_zero_outside_range() {
    let pos = _pos();
    assert(dollar_gamma(pos, 1000_u256 * ONE_E18) == 0_u256, 'dgamma below = 0');
    assert(dollar_gamma(pos, 3000_u256 * ONE_E18) == 0_u256, 'dgamma above = 0');
}

#[test]
fn test_sigma_fee_returns_zero_for_zero_inputs() {
    let result = sigma_fee(0_u256, 100_u256, 2000_u256, 86400_u256);
    assert(result == 0_u256, 'zero fees -> zero sigma_fee');
}

#[test]
fn test_position_value_in_range() {
    let pos = _pos();
    let v = position_value(pos, 2000_u256 * ONE_E18);
    assert(v > 0_u256, 'value must be > 0');
}
