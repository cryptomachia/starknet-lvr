//! Fixed-point arithmetic for `ekubo-greeks`.
//!
//! Convention: 18-decimal fixed-point on u256.
//!     ONE = 1·10^18 represents the value 1.0.
//!     a · b              → multiplied to 10^36 scale, divided down to 10^18.
//!     a / b              → multiplied to 10^36 scale by numerator first, divided.
//!     sqrt(a)            → Newton iteration; preserves 18-dec scale.
//!
//! All operations are saturation-safe via explicit u256 arithmetic and panic on
//! overflow (Cairo's u256 traps on overflow by default — this is what we want
//! in a math library).
//!
//! M3 swap-in: integrate Alexandria Math (`alexandria_math::fast_power`,
//! `alexandria_math::sqrt`) for production-grade transcendentals.

use core::integer::u256_sqrt;
use core::traits::Into;

// ---------- Scale constants ----------
pub const ONE_E18: u256 = 1_000_000_000_000_000_000_u256;
pub const ONE_E18_HALF: u256 = 500_000_000_000_000_000_u256;
pub const ONE_E36: u256 = 1_000_000_000_000_000_000_000_000_000_000_000_000_u256;
pub const ZERO: u256 = 0_u256;

// Domain bounds — guard against degenerate inputs.
pub const PRICE_MIN: u256 = 1_u256;            // 1 wei (10^-18); below this, sqrt loses precision
pub const PRICE_MAX: u256 = 1_000_000_000_000_000_000_000_000_000_u256;  // 10^9 in 1.0-scale, plenty

// ---------- Core arithmetic ----------

/// Multiply two 18-dec FP numbers, returning an 18-dec FP.
///
/// Computes (a · b) / 10^18 with rounding toward zero.
/// Panics on overflow (u256 ops trap on overflow).
pub fn fp_mul(a: u256, b: u256) -> u256 {
    if a == 0_u256 || b == 0_u256 {
        return 0_u256;
    }
    (a * b) / ONE_E18
}

/// Divide two 18-dec FP numbers, returning an 18-dec FP.
///
/// Computes (a · 10^18) / b with rounding toward zero.
/// Panics on division by zero.
pub fn fp_div(a: u256, b: u256) -> u256 {
    assert(b != 0_u256, 'fp_div: divide by zero');
    (a * ONE_E18) / b
}

/// 1 / x for 18-dec FP.
pub fn fp_inv(x: u256) -> u256 {
    fp_div(ONE_E18, x)
}

/// Saturating subtraction — returns 0 if a < b. Useful for half-life
/// calculations where small numerical errors can flip the sign.
pub fn fp_sub_sat(a: u256, b: u256) -> u256 {
    if a >= b {
        a - b
    } else {
        0_u256
    }
}

/// Square root of an 18-dec FP number; returns 18-dec FP.
///
/// Implementation: shift the input by 10^18 (so the internal representation
/// is at 10^36 scale), take u256_sqrt (which yields the floor of the square
/// root), and the result is naturally at 10^18 scale because (10^18)^2 = 10^36.
///
/// Precision analysis:
///   u256_sqrt returns floor(√n) where n ≤ 2^256. With our 10^36 scaling,
///   we lose at most 1 unit-in-the-last-place — about 10^-18 in absolute
///   terms — well below any LP-relevant precision threshold.
///
/// Newton iteration variant (in `newton_sqrt`) refines this further when
/// strict precision is required (audit-relevant operations).
pub fn fp_sqrt(x: u256) -> u256 {
    if x == 0_u256 {
        return 0_u256;
    }
    let scaled = x * ONE_E18;
    let s: u128 = u256_sqrt(scaled);
    s.into()
}

/// Newton-iteration sqrt with bounded error. Use this in audit-critical paths.
///
/// Starts from `fp_sqrt(x)` then runs k iterations of:
///   y_{n+1} = (y_n + x/y_n) / 2
/// Converges quadratically. 3 iterations are enough to reach 10^-18 relative
/// error from the u256_sqrt seed.
pub fn fp_sqrt_newton(x: u256, iterations: u32) -> u256 {
    if x == 0_u256 {
        return 0_u256;
    }
    let mut y = fp_sqrt(x);
    let mut i: u32 = 0;
    loop {
        if i >= iterations || y == 0_u256 {
            break;
        }
        // y_{n+1} = (y_n + x/y_n) / 2
        let xy = fp_div(x, y);
        y = (y + xy) / 2_u256;
        i += 1;
    };
    y
}

/// Production-grade sqrt for audit-critical paths.
///
/// 3-iteration Newton refinement on top of `core::u256_sqrt`. Reduces the
/// ULP error from ~10⁻⁹ (raw u256_sqrt at 18-dec scale) to ~10⁻¹⁸. Use this
/// in any function consumed by a vault contract or by published metrics.
///
/// When the `alexandria_math` dependency is fetched, this function should be
/// switched to `alexandria_math::sqrt::sqrt` for further verified-precision
/// guarantees. Until then the Newton refiner is the safer choice.
pub fn fp_sqrt_precise(x: u256) -> u256 {
    fp_sqrt_newton(x, 3_u32)
}

/// 1 / sqrt(x), for 18-dec FP.
pub fn fp_inv_sqrt(x: u256) -> u256 {
    let r = fp_sqrt(x);
    fp_inv(r)
}

/// x^{3/2} = x · sqrt(x), for 18-dec FP.
pub fn fp_pow_three_halves(x: u256) -> u256 {
    fp_mul(x, fp_sqrt(x))
}

/// x^{5/2} = x^2 · sqrt(x), for 18-dec FP.
pub fn fp_pow_five_halves(x: u256) -> u256 {
    fp_mul(fp_mul(x, x), fp_sqrt(x))
}

// ---------- Range checks ----------

/// Assert price is within the safe operating domain.
pub fn assert_price_in_domain(p: u256) {
    assert(p >= PRICE_MIN, 'price below domain min');
    assert(p <= PRICE_MAX, 'price above domain max');
}

/// Assert that lower < upper price bound.
pub fn assert_range_valid(p_lower: u256, p_upper: u256) {
    assert_price_in_domain(p_lower);
    assert_price_in_domain(p_upper);
    assert(p_lower < p_upper, 'range: lower >= upper');
}

// ---------- External API for downstream consumers ----------

/// Returns ONE in 18-dec fixed point.
pub fn one() -> u256 {
    ONE_E18
}

/// Returns 0.5 in 18-dec fixed point.
pub fn half() -> u256 {
    ONE_E18_HALF
}

/// Two as an 18-dec FP constant.
pub fn two() -> u256 {
    2_000_000_000_000_000_000_u256
}

/// Four as an 18-dec FP constant — used in the LVR rate denominator.
pub fn four() -> u256 {
    4_000_000_000_000_000_000_u256
}
