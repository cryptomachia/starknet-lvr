//! Impermanent loss against multiple baselines.
//!
//! Three reference baselines:
//!   1. HODL — keep the (x_0, y_0) inventory at p_0.
//!   2. Passive token1 (e.g., USDC) — V_LP at t versus V_LP at open.
//!   3. Pure token0 HODL — buy ETH at p_0, hold until p_t.
//!
//! IL_HODL is the standard v3 metric. IL_passive is what TradFi compares
//! against (the cash baseline). IL_token0 is the "I should have just held ETH"
//! comparison. Pick the right one for your audience.

use super::math::fixed_point::{ONE_E18, fp_mul, fp_div};
use super::position::{Position, position_amounts, position_value};

/// IL versus HODL (the inventory at open, marked at p_t).
///
///   IL_HODL = V_LP(p_t) − V_HODL(p_t)
///   V_HODL = y_0 + x_0 · p_t
///
/// Returns absolute magnitude (sign documented: negative when LP underperforms).
pub fn impermanent_loss_vs_hodl_abs(pos: Position, p_t: u256, p_0: u256) -> u256 {
    let amts_0 = position_amounts(pos, p_0);
    let v_hodl = amts_0.y_token1 + fp_mul(amts_0.x_token0, p_t);
    let v_lp = position_value(pos, p_t);
    if v_lp >= v_hodl {
        v_lp - v_hodl
    } else {
        v_hodl - v_lp
    }
}

/// IL versus a passive token1 (e.g., USDC) baseline.
///
///   IL_passive = V_LP(p_t) − V_open
///
/// where V_open is the position's value at open (in token1). The LP loses
/// versus pure cash when the AMM eats their inventory through arbitrage,
/// regardless of price direction.
pub fn impermanent_loss_vs_passive_abs(pos: Position, p_t: u256, p_0: u256) -> u256 {
    let v_lp = position_value(pos, p_t);
    let v_open = position_value(pos, p_0);
    if v_lp >= v_open {
        v_lp - v_open
    } else {
        v_open - v_lp
    }
}

/// IL versus a passive HODL of just token0 (the "I should have held ETH" view).
///
///   IL_eth = V_LP(p_t) − x_0_equiv · p_t
///
/// where x_0_equiv = V_open / p_0 is the number of token0 the LP could have
/// bought at open with their initial capital.
pub fn impermanent_loss_vs_token0_holdl_abs(
    pos: Position, p_t: u256, p_0: u256,
) -> u256 {
    let v_open = position_value(pos, p_0);
    let x_0_equiv = fp_div(v_open, p_0);
    let v_token0_hodl = fp_mul(x_0_equiv, p_t);
    let v_lp = position_value(pos, p_t);
    if v_lp >= v_token0_hodl {
        v_lp - v_token0_hodl
    } else {
        v_token0_hodl - v_lp
    }
}
