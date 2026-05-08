"""
Fee-implied volatility σ_fee — the volatility level at which expected fees over
a window equal expected LVR over the same window.

For a CL position with **constant fee tier** f, in-range, with constant liquidity L:
    expected_fees(σ, Δt)   = α(f, p) · Δt              # ∝ trade volume × fee, σ-independent
    expected_LVR(σ, Δt)    = (σ²/4) · L · √p · Δt       # σ²-quadratic

The fixed-point reduces to a closed form **for the window-aggregate**: given observed
fees F over Δt and observed time-weighted (L, √p, Δt), σ_fee solves
    F = (σ_fee² / 4) · L · √p · Δt
⇒   σ_fee = sqrt( 4 F / (L · √p · Δt) )

For pools with **dynamic-fee extensions** the relationship between F and σ becomes
nontrivial (extension state φ couples them). The general solver below uses Brent's
method on a user-supplied `expected_fees(σ)` callable.

Conventions:
- F is in the same numéraire as the LVR (token1).
- Δt and σ share a time basis. We use seconds and annualize at the end.
"""

from __future__ import annotations
import math
from typing import Callable, Optional
try:
    from scipy.optimize import brentq  # type: ignore
except ImportError:
    brentq = None  # we provide a bisection fallback


SECONDS_PER_YEAR = 365 * 24 * 3600.0


def sigma_fee_closed_form(
    fees_token1: float,
    L: float,
    p: float,
    dt_seconds: float,
    annualized: bool = True,
) -> float:
    """Closed-form σ_fee for the constant-fee case.

    Args:
        fees_token1: total fees (in token1) collected over the window.
        L:           the v3 liquidity invariant (NOT TVL).
        p:           time-weighted average price during the window.
        dt_seconds:  window length in seconds.
        annualized:  if True, return σ on an annualized basis (default).

    Returns:
        σ_fee on the requested basis. Returns NaN if inputs are degenerate.
    """
    if fees_token1 <= 0 or L <= 0 or p <= 0 or dt_seconds <= 0:
        return float("nan")
    sigma_per_sqrt_dt = math.sqrt(4.0 * fees_token1 / (L * math.sqrt(p) * dt_seconds))
    if annualized:
        return sigma_per_sqrt_dt * math.sqrt(SECONDS_PER_YEAR / SECONDS_PER_YEAR)  # already in 1/sqrt(time) basis
    return sigma_per_sqrt_dt * math.sqrt(dt_seconds)


def sigma_fee_from_tvl_proxy(
    fees_usd: float,
    tvl_usd: float,
    dt_seconds: float,
) -> float:
    """σ_fee from the TVL proxy used in the scoping figure.

    Substitute lvr_rate_proxy_from_tvl: F = (σ²/4) · TVL · Δt
    ⇒ σ_fee = sqrt(4 F / (TVL · Δt)),  annualized via Δt → year.

    This is what's computable from the daily Ekubo API (no L extraction).
    Identical convention to the scoping figure's `fee_yield_ann` once you
    note: fee_yield_ann = F·(year/Δt) / TVL, so σ_fee_ann² = 4·fee_yield_ann.

    Equivalently: σ_fee_ann = 2 · √(fee_yield_ann).
    """
    if fees_usd <= 0 or tvl_usd <= 0 or dt_seconds <= 0:
        return float("nan")
    fee_yield_ann = (fees_usd * SECONDS_PER_YEAR / dt_seconds) / tvl_usd
    return 2.0 * math.sqrt(fee_yield_ann)


def sigma_fee_solve(
    expected_fees: Callable[[float], float],
    expected_lvr: Callable[[float], float],
    sigma_low: float = 1e-4,
    sigma_high: float = 10.0,
    tol: float = 1e-8,
) -> Optional[float]:
    """General solver: find σ such that expected_fees(σ) == expected_lvr(σ).

    Useful when fees depend on σ via a dynamic-fee extension state. Uses Brent's
    method if scipy is available, else a simple bisection.

    Returns None if no sign change is detected on the bracket [sigma_low, sigma_high].
    """
    f = lambda s: expected_fees(s) - expected_lvr(s)  # noqa: E731
    f_lo, f_hi = f(sigma_low), f(sigma_high)
    if f_lo == 0:
        return sigma_low
    if f_hi == 0:
        return sigma_high
    if f_lo * f_hi > 0:
        return None  # no root in bracket
    if brentq is not None:
        return float(brentq(f, sigma_low, sigma_high, xtol=tol))
    # bisection fallback
    a, b = sigma_low, sigma_high
    fa, fb = f_lo, f_hi
    while b - a > tol:
        m = 0.5 * (a + b)
        fm = f(m)
        if fa * fm < 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return 0.5 * (a + b)
