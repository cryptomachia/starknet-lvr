"""
Greeks for a v3-style / Ekubo concentrated liquidity position.

Reference:
- Lambert, G. (2021). "Understanding the Value of Uniswap v3 Liquidity Positions."
- Cartea, Drissi, Monga (2023). "Decentralised Finance and AMM: Predictable Loss
  and Optimal Liquidity Provision." arXiv:2309.08431.

Conventions:
- Position holds liquidity `L` between price bounds [p_a, p_b], p_a < p_b.
- Prices `p` are quoted as token1 per token0 (e.g., USDC per ETH).
- All amounts and value are returned in **token1 numéraire**.
- Greeks are reported wrt the price `p` (so `delta` has units of token0
  inventory equivalent at price `p`; this is the standard hedge-ratio).

Closed forms (derivation: Lambert 2021):
    in-range (p_a < p < p_b):
        x(p) = L (1/√p − 1/√p_b)              # token0 amount
        y(p) = L (√p − √p_a)                   # token1 amount
        V(p) = y + x·p                         # value in token1
             = L (2√p − √p_a − p/√p_b)
        ∂V/∂p = x(p)                           # delta — Lambert's identity
        ∂²V/∂p² = -L / (2 p^{3/2})             # gamma (negative — LP is short gamma)

    below range (p ≤ p_a): all token0; x = L(1/√p_a − 1/√p_b), y = 0
    above range (p ≥ p_b): all token1; x = 0,                 y = L(√p_b − √p_a)
"""

from __future__ import annotations
import math
from typing import Tuple, Iterable

import numpy as np

# Re-export the canonical Position from the domain layer so all existing
# imports `from lvr_lab.compute.greeks import Position` keep working.
from ..domain.position import Position  # noqa: F401  (re-exported)
from ..domain.portfolio import Portfolio, PortfolioGreeks  # noqa: F401


def position_amounts(pos: Position, p: float) -> Tuple[float, float]:
    """Return (x, y) — the token0 and token1 amounts a CL position holds at price p."""
    if p <= 0:
        raise ValueError(f"price must be positive; got {p}")
    sqrt_p = math.sqrt(p)
    sqrt_pa = math.sqrt(pos.p_a)
    sqrt_pb = math.sqrt(pos.p_b)
    if p <= pos.p_a:
        x = pos.L * (1 / sqrt_pa - 1 / sqrt_pb)
        y = 0.0
    elif p >= pos.p_b:
        x = 0.0
        y = pos.L * (sqrt_pb - sqrt_pa)
    else:
        x = pos.L * (1 / sqrt_p - 1 / sqrt_pb)
        y = pos.L * (sqrt_p - sqrt_pa)
    return x, y


def position_value(pos: Position, p: float) -> float:
    """Position value in token1 numéraire at price p."""
    x, y = position_amounts(pos, p)
    return y + x * p


def delta(pos: Position, p: float) -> float:
    """∂V/∂p — equals x(p) (Lambert's identity for v3 LPs).

    Interpretation: how many token1 the position gains per unit price increase.
    To delta-hedge with a perp on the underlying token0 priced in token1:
        short_size_in_token0 = delta(pos, p) / 1   (since perp has delta 1 token1/token0)
    """
    x, _ = position_amounts(pos, p)
    return x


def gamma(pos: Position, p: float) -> float:
    """∂²V/∂p². Negative in-range; zero outside (position is single-sided)."""
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return -pos.L / (2 * p ** 1.5)


def impermanent_loss(pos: Position, p_t: float, p_0: float) -> float:
    """IL relative to a HODL portfolio that started at p_0 with the same initial split.

    IL = V_LP(p_t) − V_HODL(p_t),  in token1.

    where V_HODL holds the (x_0, y_0) inventory the LP started with.
    """
    x0, y0 = position_amounts(pos, p_0)
    v_hodl = y0 + x0 * p_t
    return position_value(pos, p_t) - v_hodl


# ---------- Convenience: liquidity from desired USD ----------
def liquidity_from_value(value_usd: float, p: float, p_a: float, p_b: float) -> float:
    """Solve for L such that the resulting in-range position is worth `value_usd` at price p.

    V(p) = L (2√p − √p_a − p/√p_b),  so L = value / (2√p − √p_a − p/√p_b).
    """
    if not (p_a < p < p_b):
        raise ValueError("price must be strictly within [p_a, p_b] for in-range value")
    coeff = 2 * math.sqrt(p) - math.sqrt(p_a) - p / math.sqrt(p_b)
    if coeff <= 0:
        raise ValueError("degenerate position (coefficient ≤ 0)")
    return value_usd / coeff


# ---------- Higher-order Greek: speed (∂³V/∂p³) ----------
def speed(pos: Position, p: float) -> float:
    """Third derivative of position value wrt price.

    ∂³V/∂p³ = ∂Γ/∂p = 3·L / (4 · p^{5/2})  (in-range; positive)

    Useful for capturing the gamma-of-gamma in long-horizon hedging or for
    the quadratic-approximation error term ε² ∂³V/∂p³ / 6.
    """
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return 3.0 * pos.L / (4.0 * p ** 2.5)


# ---------- Vega: how much LP value changes per unit σ ----------
# A v3 LP position itself has zero vega in the strict Black-Scholes sense
# (it doesn't trade vol). But the σ-implied LVR cost has a vega:
#   ∂(LVR_rate)/∂σ = (σ/2) · L · √p
# This tells us how the LP's expected loss-rate changes per unit σ.
def lvr_vega(pos: Position, p: float, sigma: float) -> float:
    """∂(LVR_rate)/∂σ — sensitivity of expected LVR rate to volatility.

    A 1-unit (annualized) increase in σ raises the LVR rate by:
        2 · σ · L · √p / 4 = σ · L · √p / 2.
    Multiply by the holding period (in the σ-time-basis) to get total dollar impact.
    """
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return sigma * pos.L * math.sqrt(p) / 2.0


# ---------- Dollar Greeks (multiply by current TVL or position value) ----------
def dollar_gamma(pos: Position, p: float) -> float:
    """USD Γ — what you actually lose for a (Δp / p)² move.

    Standard: 0.5 · Γ · (Δp)² is the second-order P&L term. Convert to
    dollar-equivalent by multiplying with p² (so the input is a percent move):
        dollar_gamma(p) = 0.5 · |Γ(p)| · p²
    Reading: an LP with dollar_gamma=$X loses ~$X for every 100% squared move,
    i.e., $X·(1%)²·10000 = $X for a 1% move (rough rule of thumb).
    """
    if p <= pos.p_a or p >= pos.p_b:
        return 0.0
    return 0.5 * (pos.L / (2 * p ** 1.5)) * (p ** 2)


# ---------- IL term structure ----------
def il_term_structure(pos: Position, price_path: Iterable[Tuple[float, float]],
                      p_0: float) -> dict:
    """Compute IL at multiple horizons over a price path.

    Args:
        pos:        the LP position
        price_path: iterable of (timestamp, price) tuples, sorted by timestamp
        p_0:        the price at which the position was opened

    Returns:
        dict with keys '1d', '7d', '30d' (whichever fit in the path) → IL value
        in token1 numéraire. NaN if the horizon exceeds the available path.
    """
    rows = sorted(price_path, key=lambda r: r[0])
    if not rows:
        return {}
    t0 = rows[0][0]
    horizons_seconds = {"1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
    out = {}
    for label, h_s in horizons_seconds.items():
        target_t = t0 + h_s
        # find the first row at or after target_t
        candidates = [r for r in rows if r[0] >= target_t]
        if not candidates:
            out[label] = float("nan")
            continue
        p_t = candidates[0][1]
        out[label] = impermanent_loss(pos, p_t, p_0)
    return out


# ---------- Vectorized variants for backtest performance ----------
def value_curve(pos: Position, prices: np.ndarray) -> np.ndarray:
    """V(p) over an array of prices. Vectorized, ~100× faster than per-call."""
    sqrt_p = np.sqrt(prices)
    sqrt_pa = math.sqrt(pos.p_a)
    sqrt_pb = math.sqrt(pos.p_b)
    below = prices <= pos.p_a
    above = prices >= pos.p_b
    in_range = ~(below | above)
    out = np.zeros_like(prices, dtype=float)
    # below range: all token0; V = x · p = L (1/√pa − 1/√pb) · p
    out[below] = pos.L * (1.0 / sqrt_pa - 1.0 / sqrt_pb) * prices[below]
    # above range: all token1; V = L (√pb − √pa)
    out[above] = pos.L * (sqrt_pb - sqrt_pa)
    # in range: V = L (2√p − √pa − p/√pb)
    if in_range.any():
        sp = sqrt_p[in_range]
        p = prices[in_range]
        out[in_range] = pos.L * (2.0 * sp - sqrt_pa - p / sqrt_pb)
    return out


def delta_curve(pos: Position, prices: np.ndarray) -> np.ndarray:
    """δ(p) = x(p) over an array of prices."""
    sqrt_p = np.sqrt(prices)
    sqrt_pa = math.sqrt(pos.p_a)
    sqrt_pb = math.sqrt(pos.p_b)
    below = prices <= pos.p_a
    above = prices >= pos.p_b
    in_range = ~(below | above)
    out = np.zeros_like(prices, dtype=float)
    out[below] = pos.L * (1.0 / sqrt_pa - 1.0 / sqrt_pb)
    # above: x = 0
    if in_range.any():
        out[in_range] = pos.L * (1.0 / sqrt_p[in_range] - 1.0 / sqrt_pb)
    return out


def gamma_curve(pos: Position, prices: np.ndarray) -> np.ndarray:
    """Γ(p) over an array of prices; zero outside range."""
    in_range = (prices > pos.p_a) & (prices < pos.p_b)
    out = np.zeros_like(prices, dtype=float)
    out[in_range] = -pos.L / (2.0 * prices[in_range] ** 1.5)
    return out


# ---------- Portfolio batch operation ----------
def portfolio_value_curve(portfolio: Portfolio, prices: np.ndarray) -> np.ndarray:
    """Aggregate value curve for a Portfolio of positions. Linear sum."""
    if len(portfolio) == 0:
        return np.zeros_like(prices, dtype=float)
    out = np.zeros_like(prices, dtype=float)
    for pos in portfolio:
        out += value_curve(pos, prices)
    return out


def portfolio_delta_curve(portfolio: Portfolio, prices: np.ndarray) -> np.ndarray:
    if len(portfolio) == 0:
        return np.zeros_like(prices, dtype=float)
    out = np.zeros_like(prices, dtype=float)
    for pos in portfolio:
        out += delta_curve(pos, prices)
    return out


def portfolio_gamma_curve(portfolio: Portfolio, prices: np.ndarray) -> np.ndarray:
    if len(portfolio) == 0:
        return np.zeros_like(prices, dtype=float)
    out = np.zeros_like(prices, dtype=float)
    for pos in portfolio:
        out += gamma_curve(pos, prices)
    return out


# ---------- Range-management metrics ----------
def time_in_range(prices: np.ndarray, pos: Position) -> float:
    """Fraction of observations where position is in-range. ∈ [0, 1]."""
    if len(prices) == 0:
        return float("nan")
    in_range = (prices > pos.p_a) & (prices < pos.p_b)
    return float(in_range.mean())


def expected_capital_efficiency(pos: Position, p_ref: float) -> float:
    """L / capital_required at p_ref — how much "v3 liquidity" each USD buys.

    For a uniform-band ±5%: ~10× a v2 LP at the same price. For a tight
    concentrated-active: 50–100×.
    """
    if not (pos.p_a < p_ref < pos.p_b):
        return float("nan")
    coeff = 2 * math.sqrt(p_ref) - math.sqrt(pos.p_a) - p_ref / math.sqrt(pos.p_b)
    return pos.L / coeff if coeff > 0 else float("nan")
