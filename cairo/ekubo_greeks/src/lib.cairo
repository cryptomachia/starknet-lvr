//! ekubo-greeks — Cairo library implementing position math for Ekubo
//! concentrated-liquidity positions on Starknet.
//!
//! Public API surface:
//!
//!   Position math:
//!     - new_position(L, p_a, p_b)           # constructor with invariants
//!     - position_amounts(pos, p)            # (x_token0, y_token1)
//!     - position_value(pos, p)              # V in token1
//!     - position_state(pos, p)              # below/in/above range
//!     - range_width_pct, range_geomean      # geometry
//!
//!   Greeks:
//!     - delta_token0(pos, p)                # ∂V/∂p = x(p)
//!     - gamma_abs(pos, p)                   # |∂²V/∂p²| = L/(2 p^{3/2})
//!     - speed_abs(pos, p)                   # |∂³V/∂p³| = 3L/(4 p^{5/2})
//!     - lvr_vega(pos, p, σ)                 # ∂(LVR_rate)/∂σ
//!     - dollar_gamma(pos, p)                # 0.5 |Γ| p²
//!     - capital_efficiency(pos, p_ref)      # L / capital_required
//!
//!   LVR:
//!     - marginal_liquidity(pos, p)
//!     - lvr_rate_in_range_token1(pos, p, σ)
//!     - lvr_integrated_constant_sigma(pos, ts, prices, σ)
//!     - lvr_per_swap_empirical(Δq, p_pool, p_ref)
//!     - sigma_fee(fees, L, p, Δt)           # closed-form σ_fee solver
//!
//!   Impermanent Loss:
//!     - impermanent_loss_vs_hodl_abs(pos, p_t, p_0)
//!     - impermanent_loss_vs_passive_abs(pos, p_t, p_0)
//!     - impermanent_loss_vs_token0_holdl_abs(pos, p_t, p_0)
//!
//! All values are 18-decimal fixed-point on u256. The reference Python
//! implementation in `src/lvr_lab/compute/greeks.py` is the canonical
//! cross-validation oracle.
//!
//! Status: v0.5 (M2 milestone). v1.0 ships post light third-party security
//! review (Nethermind / Cairo Security Clan, scope-limited to library
//! correctness).

pub mod math;
pub mod position;
pub mod greeks;
pub mod lvr;
pub mod il;
