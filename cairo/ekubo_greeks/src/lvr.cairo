//! Loss-Versus-Rebalancing — instantaneous + integrated forms.
//!
//! For an in-range CL position with constant L:
//!   marginal_liquidity ℓ(p) = L / (2 √p)
//!   LVR_rate(p)             = (σ² / 2) · p · ℓ(p) = σ² · L · √p / 4
//!
//! σ and the result share a time basis (caller's responsibility).

use super::math::fixed_point::{
    ONE_E18, fp_mul, fp_div, fp_sqrt, two, four,
};
use super::position::Position;

/// ℓ(p) = L / (2 √p) for in-range; 0 otherwise. 18-dec FP.
pub fn marginal_liquidity(pos: Position, p: u256) -> u256 {
    if p <= pos.p_a || p >= pos.p_b {
        return 0_u256;
    }
    let sqrt_p = fp_sqrt(p);
    fp_div(pos.L, fp_mul(two(), sqrt_p))
}

/// LVR_rate(p) = σ² · L · √p / 4  (token1 per unit time, in-range).
///
/// σ on the same time basis as the output (annualized → per-year rate).
pub fn lvr_rate_in_range_token1(pos: Position, p: u256, sigma: u256) -> u256 {
    if p <= pos.p_a || p >= pos.p_b {
        return 0_u256;
    }
    let sigma_sq = fp_mul(sigma, sigma);
    let sqrt_p = fp_sqrt(p);
    let numer = fp_mul(fp_mul(sigma_sq, pos.L), sqrt_p);
    fp_div(numer, four())
}

/// Trapezoidal-rule integrated LVR over a known price path.
///
/// Caller provides arrays of (timestamp, price). The function walks pairs
/// and accumulates `0.5 · (rate_t + rate_{t+1}) · Δt` using a constant σ.
///
/// Returns the integrated LVR in token1; 18-dec FP.
pub fn lvr_integrated_constant_sigma(
    pos: Position,
    timestamps: Array<u256>,
    prices: Array<u256>,
    sigma: u256,
) -> u256 {
    let n = timestamps.len();
    if n < 2 || prices.len() != n {
        return 0_u256;
    }
    let mut total: u256 = 0_u256;
    let mut i: u32 = 1;
    loop {
        if i >= n {
            break;
        }
        let t0 = *timestamps.at(i - 1);
        let t1 = *timestamps.at(i);
        let p0 = *prices.at(i - 1);
        let p1 = *prices.at(i);
        if t1 <= t0 {
            i += 1;
            continue;
        }
        let dt = t1 - t0;
        let r0 = lvr_rate_in_range_token1(pos, p0, sigma);
        let r1 = lvr_rate_in_range_token1(pos, p1, sigma);
        let avg_rate = (r0 + r1) / two();
        total += fp_mul(avg_rate, dt);
        i += 1;
    };
    total
}

/// Empirical block-level LVR for a single Swap event.
///
/// LVR per swap (Milionis et al. 2022 empirical form):
///   |Δinventory| · |p_ref − p_pool|
///
/// where Δinventory is the LP's token0 outflow (signed) and p_ref is an
/// off-chain reference price (Pragma / Coinbase / etc.).
///
/// Use this when you have swap-level data and a reference oracle. Sum
/// across all swaps in a window to get the realized LVR.
///
/// Returns absolute magnitude in token1 numéraire.
pub fn lvr_per_swap_empirical(
    delta_inventory_token0: u256,
    p_pool: u256,
    p_ref: u256,
) -> u256 {
    let price_gap = if p_ref >= p_pool {
        p_ref - p_pool
    } else {
        p_pool - p_ref
    };
    fp_mul(delta_inventory_token0, price_gap)
}

/// LVR-vs-fee-yield wedge in σ-space.
///
/// σ_fee = √(4 · F / (L · √p · Δt))   from   F = (σ²/4) · L · √p · Δt
///
/// Returns σ_fee in 18-dec FP. Δt and σ on the same time basis.
pub fn sigma_fee(fees_token1: u256, l: u256, p: u256, dt: u256) -> u256 {
    if fees_token1 == 0_u256 || l == 0_u256 || p == 0_u256 || dt == 0_u256 {
        return 0_u256;
    }
    let sqrt_p = fp_sqrt(p);
    let denom = fp_mul(fp_mul(l, sqrt_p), dt);
    let four_f = fp_mul(four(), fees_token1);
    let inner = fp_div(four_f, denom);
    fp_sqrt(inner)
}
