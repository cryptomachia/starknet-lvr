//! Position Greeks — Δ, Γ, vega, IL, dollar Greeks, capital efficiency.
//!
//! Cross-validated against `src/lvr_lab/compute/greeks.py` (reference
//! oracle); see `tests/property/` for property-based equivalence tests.
//!
//! Conventions:
//!   - All values 18-dec FP on u256.
//!   - All quantities in token1 numéraire unless suffixed `_token0`.
//!   - Greeks are wrt the price `p`.

use super::math::fixed_point::{
    ONE_E18, fp_mul, fp_div, fp_sqrt, fp_pow_three_halves, fp_pow_five_halves,
    two, four,
};
use super::position::{Position, position_amounts, Amounts, position_value};

/// ∂V/∂p = x(p) — Lambert's identity for v3 LPs.
///
/// Returns the position's token0 inventory at price p. To delta-hedge with
/// a perp on token0, short this many token0.
pub fn delta_token0(pos: Position, p: u256) -> u256 {
    let amts = position_amounts(pos, p);
    amts.x_token0
}

/// |∂²V/∂p²| = L / (2 p^{3/2}). Returns absolute value.
///
/// **Sign:** in-range gamma is *negative* (LP is short gamma). The Cairo type
/// system doesn't have signed integers natively at u256, so we return the
/// absolute value and document the sign convention here. Downstream consumers
/// know to treat this as negative for in-range positions.
///
/// Outside the range, gamma is zero (position is single-sided).
pub fn gamma_abs(pos: Position, p: u256) -> u256 {
    if p <= pos.p_a || p >= pos.p_b {
        return 0_u256;
    }
    let p_three_halves = fp_pow_three_halves(p);
    let two_p_three_halves = fp_mul(two(), p_three_halves);
    fp_div(pos.L, two_p_three_halves)
}

/// |∂³V/∂p³| = 3·L / (4·p^{5/2}). Returns absolute value.
///
/// "Speed" — the third derivative. Useful for cubic-correction terms in
/// long-horizon hedging. Positive in-range; zero outside.
pub fn speed_abs(pos: Position, p: u256) -> u256 {
    if p <= pos.p_a || p >= pos.p_b {
        return 0_u256;
    }
    let p_five_halves = fp_pow_five_halves(p);
    let four_p_five_halves = fp_mul(four(), p_five_halves);
    let three_l = fp_mul(3_000_000_000_000_000_000_u256, pos.L);
    fp_div(three_l, four_p_five_halves)
}

/// ∂(LVR_rate)/∂σ — vega of expected LVR rate.
///
///   LVR_rate = σ² · L · √p / 4
///   ∂/∂σ     = σ · L · √p / 2
///
/// Output: 18-dec FP, in the same time-basis as σ.
pub fn lvr_vega(pos: Position, p: u256, sigma: u256) -> u256 {
    if p <= pos.p_a || p >= pos.p_b {
        return 0_u256;
    }
    let sqrt_p = fp_sqrt(p);
    let s_l = fp_mul(sigma, pos.L);
    let s_l_sqrt_p = fp_mul(s_l, sqrt_p);
    fp_div(s_l_sqrt_p, two())
}

/// IL versus a HODL portfolio (absolute magnitude).
///
///   IL = V_LP(p_t) − V_HODL(p_t)
///   V_HODL = y_0 + x_0 · p_t
///
/// **Sign:** IL is negative when LP underperforms HODL (the typical case).
/// We return the absolute magnitude.
pub fn impermanent_loss_abs(pos: Position, p_t: u256, p_0: u256) -> u256 {
    let amts_0 = position_amounts(pos, p_0);
    let v_hodl = amts_0.y_token1 + fp_mul(amts_0.x_token0, p_t);
    let v_lp = position_value(pos, p_t);
    if v_lp >= v_hodl {
        v_lp - v_hodl
    } else {
        v_hodl - v_lp
    }
}

/// Dollar gamma — what the LP loses for a (Δp/p)² move, scaled to token1.
///
///   dollar_gamma = 0.5 · |Γ| · p²
pub fn dollar_gamma(pos: Position, p: u256) -> u256 {
    let g = gamma_abs(pos, p);
    if g == 0_u256 {
        return 0_u256;
    }
    let p_sq = fp_mul(p, p);
    fp_div(fp_mul(g, p_sq), two())
}

/// Capital efficiency: L / capital_required(p_ref).
///
/// For a uniform-band ±5% at p_ref ≈ 2000:
///   coefficient = 2·√p − √p_a − p/√p_b
///   efficiency  = L / coefficient (in 18-dec FP)
///
/// Outside the range, returns 0 (degenerate).
pub fn capital_efficiency(pos: Position, p_ref: u256) -> u256 {
    if p_ref <= pos.p_a || p_ref >= pos.p_b {
        return 0_u256;
    }
    let sqrt_p = fp_sqrt(p_ref);
    let sqrt_pa = fp_sqrt(pos.p_a);
    let sqrt_pb = fp_sqrt(pos.p_b);
    let two_sqrt_p = fp_mul(two(), sqrt_p);
    let p_over_sqrt_pb = fp_div(p_ref, sqrt_pb);
    if two_sqrt_p <= sqrt_pa + p_over_sqrt_pb {
        return 0_u256;
    }
    let coeff = two_sqrt_p - sqrt_pa - p_over_sqrt_pb;
    fp_div(pos.L, coeff)
}
