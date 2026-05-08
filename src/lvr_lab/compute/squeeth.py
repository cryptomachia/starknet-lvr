"""
Power-perpetual ("Squeeth") math: hedging the LP's negative gamma with a
constant-gamma instrument.

Reference: Paradigm Research, "Power Perpetuals" (White et al. 2021).

Squeeth tracks ETH²:
    payoff(p) = p²
    Δ_squeeth = 2p             (in token0=USD-quoted; in ETH numéraire it's 2)
    Γ_squeeth = 2              (constant)

For a v3 LP between [p_a, p_b] with liquidity L, in-range:
    Γ_LP = -L / (2 p^{3/2})   (negative; LP short gamma)

Gamma-flat hedge: long Squeeth in size n_sqth = -Γ_LP / Γ_squeeth = L / (4 p^{3/2}).

Residual delta after gamma-hedging:
    Δ_residual = Δ_LP + n_sqth · Δ_squeeth
              = x(p) + (L / 4 p^{3/2}) · 2p
              = x(p) + L / (2 √p)
              = (3 L) / (2 √p) - L/√p_b           (algebra; in-range)

Vault stack:
    1. Open the LP position (short gamma).
    2. Long n_sqth Squeeth contracts (offsets gamma to ~0).
    3. Short the residual delta on a vanilla perp.
    4. Pay Squeeth funding (≈ 2σ² annualized in expectation).
"""

from __future__ import annotations
import math
from dataclasses import dataclass

from .greeks import Position, position_amounts


# Squeeth's normalized greeks; the literal formulas use a normalization so
# that Δ_squeeth = 2p and Γ_squeeth = 2 in token0=USD numéraire.
SQUEETH_GAMMA = 2.0

# Default Squeeth funding rate — practically observed ≈ 2σ² where σ is annualized
# spot vol; calibrate per backtest.
DEFAULT_SQUEETH_FUNDING_APR = lambda sigma: 2.0 * sigma ** 2  # noqa: E731


def squeeth_size_to_flatten_gamma(pos: Position, p: float) -> float:
    """Squeeth contracts to long to flatten gamma (in-range only)."""
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return pos.L / (4.0 * p ** 1.5)


def residual_delta_after_squeeth(pos: Position, p: float) -> float:
    """Token0-equivalent delta remaining after gamma-flattening with Squeeth."""
    x, _ = position_amounts(pos, p)
    n_sqth = squeeth_size_to_flatten_gamma(pos, p)
    delta_squeeth = 2.0 * p  # in token0 (USD per token0 unit move)
    # n_sqth contracts contribute n_sqth · Δ_squeeth to the value derivative.
    # Convert that to token0 inventory: n_sqth · 2p / p = 2 n_sqth.
    return x + 2.0 * n_sqth


def squeeth_pnl_step(prev_n_sqth: float, prev_p: float, p: float,
                     funding_apr: float, dt_seconds: float) -> tuple[float, float]:
    """Mark-to-market a Squeeth position over one step.

    Squeeth payoff is p², so PnL on n_sqth contracts = n_sqth · (p² - prev_p²).
    Funding paid on |notional| × funding_apr × dt.
    """
    pnl = prev_n_sqth * (p ** 2 - prev_p ** 2)
    notional = abs(prev_n_sqth) * 0.5 * (prev_p ** 2 + p ** 2)
    funding = notional * funding_apr * (dt_seconds / (365 * 24 * 3600.0))
    return pnl, funding
