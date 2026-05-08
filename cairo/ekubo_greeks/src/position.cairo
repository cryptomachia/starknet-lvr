//! Position — value object for a v3-style CL position.
//!
//! Wraps (L, p_a, p_b) with invariant checks. Optional metadata fields are
//! omitted from the on-chain representation (kept as Cairo associated types
//! for memory efficiency); the Python reference layer carries the full
//! metadata for cohort tagging.

use super::math::fixed_point::{
    ONE_E18, assert_range_valid, fp_sqrt, fp_mul, fp_div,
};

#[derive(Copy, Drop, Debug, PartialEq, Serde)]
pub struct Position {
    /// Liquidity invariant L (18-dec FP).
    pub L: u256,
    /// Lower price bound (token1/token0, 18-dec FP).
    pub p_a: u256,
    /// Upper price bound (18-dec FP).
    pub p_b: u256,
}

#[derive(Copy, Drop, Debug, PartialEq, Serde)]
pub struct Amounts {
    pub x_token0: u256,
    pub y_token1: u256,
}

#[derive(Copy, Drop, Debug, PartialEq, Serde)]
pub enum PositionState {
    BelowRange,
    InRange,
    AboveRange,
}

/// Construct a Position with invariant checks.
pub fn new_position(l: u256, p_a: u256, p_b: u256) -> Position {
    assert_range_valid(p_a, p_b);
    assert(l > 0_u256, 'position: L must be positive');
    Position { L: l, p_a, p_b }
}

/// Classify position state by current price.
pub fn position_state(pos: Position, p: u256) -> PositionState {
    if p <= pos.p_a {
        PositionState::BelowRange
    } else if p >= pos.p_b {
        PositionState::AboveRange
    } else {
        PositionState::InRange
    }
}

/// Token amounts at price p. All values 18-dec FP.
pub fn position_amounts(pos: Position, p: u256) -> Amounts {
    assert(p > 0_u256, 'position_amounts: p == 0');
    let sqrt_p = fp_sqrt(p);
    let sqrt_pa = fp_sqrt(pos.p_a);
    let sqrt_pb = fp_sqrt(pos.p_b);

    if p <= pos.p_a {
        // All token0: x = L · (1/√pa − 1/√pb), y = 0
        let inv_pa = fp_div(ONE_E18, sqrt_pa);
        let inv_pb = fp_div(ONE_E18, sqrt_pb);
        let diff = inv_pa - inv_pb;
        let x = fp_mul(pos.L, diff);
        Amounts { x_token0: x, y_token1: 0_u256 }
    } else if p >= pos.p_b {
        // All token1: x = 0, y = L · (√pb − √pa)
        let diff = sqrt_pb - sqrt_pa;
        let y = fp_mul(pos.L, diff);
        Amounts { x_token0: 0_u256, y_token1: y }
    } else {
        // In range: x = L·(1/√p − 1/√pb), y = L·(√p − √pa)
        let inv_p = fp_div(ONE_E18, sqrt_p);
        let inv_pb = fp_div(ONE_E18, sqrt_pb);
        let x = fp_mul(pos.L, inv_p - inv_pb);
        let y = fp_mul(pos.L, sqrt_p - sqrt_pa);
        Amounts { x_token0: x, y_token1: y }
    }
}

/// Position value in token1 numéraire at price p.
///
/// V(p) = y + x · p
pub fn position_value(pos: Position, p: u256) -> u256 {
    let amts = position_amounts(pos, p);
    amts.y_token1 + fp_mul(amts.x_token0, p)
}

/// Width of the range as a fraction (18-dec FP).
///   width = (√p_b − √p_a) / √(p_a · p_b)
pub fn range_width_pct(pos: Position) -> u256 {
    let sqrt_pa = fp_sqrt(pos.p_a);
    let sqrt_pb = fp_sqrt(pos.p_b);
    let geomean = fp_sqrt(fp_mul(pos.p_a, pos.p_b));
    let diff = sqrt_pb - sqrt_pa;
    fp_div(diff, geomean)
}

/// Geometric-mean price of the range.
pub fn range_geomean(pos: Position) -> u256 {
    fp_sqrt(fp_mul(pos.p_a, pos.p_b))
}
