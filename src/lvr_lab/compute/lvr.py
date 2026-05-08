"""
Loss-Versus-Rebalancing (LVR) for Ekubo / v3 concentrated-liquidity positions.

Reference:
- Milionis, Moallemi, Roughgarden, Zhang (2022). "AMM and LVR." arXiv:2208.06046
- Cartea, Drissi, Monga (2023). arXiv:2309.08431

Core result for an in-range CL position with constant liquidity L:
    LVR_rate(p) = (σ² / 2) · p · ℓ(p)

where ℓ(p) is the marginal-liquidity quantity. For v3 in-range:
    x(p) = L (1/√p − 1/√p_b)
    -∂x/∂p = L / (2 p^{3/2})
    ℓ(p) := -∂x/∂p · p = L / (2√p)

⇒  LVR_rate(p) = (σ² / 2) · p · L / (2√p) = σ² · L · √p / 4    [token1/time]

Conventions:
- σ is volatility of price (token1/token0) on the **same time-unit basis** as the
  output rate. For annualized σ, the output is in token1-per-year.
- Outside the position's range, LVR_rate = 0 (position is single-sided / inert).

LVR_integrated over a window with realized price path:
- continuous form:  ∫_0^Δt LVR_rate(p_t) dt
- empirical form:   sum over per-block crossings of (LP_inventory_change × (p_ref − p_pool))
  This empirical form is implemented separately in the swap-level pipeline (M1 of grant).
"""

from __future__ import annotations
import math
from typing import Iterable, Tuple
from .greeks import Position


def marginal_liquidity(pos: Position, p: float) -> float:
    """ℓ(p) = L / (2√p)  for v3 CL in-range; 0 outside."""
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return pos.L / (2 * math.sqrt(p))


def lvr_rate_in_range(pos: Position, p: float, sigma: float) -> float:
    """Instantaneous LVR rate (token1 per unit time) at price p, vol σ.

    σ and the resulting rate must share a time basis (e.g., σ annualized → rate per year).
    """
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return (sigma ** 2) * pos.L * math.sqrt(p) / 4.0


def lvr_integrated(pos: Position, prices: Iterable[Tuple[float, float]], sigma: float) -> float:
    """Approximate LVR over a price path via the trapezoidal rule.

    `prices` is an iterable of (t, p) sorted by t. σ is constant over the path
    (use lvr_integrated_path_sigma for time-varying σ).
    """
    rows = list(prices)
    if len(rows) < 2:
        return 0.0
    rows.sort(key=lambda r: r[0])
    total = 0.0
    for (t0, p0), (t1, p1) in zip(rows, rows[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        r0 = lvr_rate_in_range(pos, p0, sigma)
        r1 = lvr_rate_in_range(pos, p1, sigma)
        total += 0.5 * (r0 + r1) * dt
    return total


def lvr_rate_proxy_from_tvl(tvl_token1: float, p: float, sigma: float) -> float:
    """Pool-level LVR rate proxy when only TVL (not L) is observable.

    For a uniform-band position covering an effective range, V(p) ≈ L · √p · k,
    where k is a profile constant (k=2 for an unbounded passive AMM, smaller
    for concentrated). Substituting L ≈ TVL / (√p · k) into the formula:

        LVR_rate ≈ (σ²/4) · TVL · k_eff

    where k_eff ∈ [0.5, 1] for typical Ekubo concentrated profiles. We take
    k_eff = 1 (the upper-bound assumption — a passive uniform-band LP) and
    document that this OVERESTIMATES LVR for concentrated positions and
    UNDERESTIMATES it for ranges narrower than ±50%.

    This proxy is what's computable from the daily Ekubo API. The swap-level
    pipeline (M1) will replace it with a position-reconstructed L.
    """
    if tvl_token1 <= 0 or p <= 0:
        return 0.0
    return (sigma ** 2) * tvl_token1 / 4.0
