"""
Optimal hedging under stochastic delays — LQ-control approximation of the
Bouchard, Han, Hu, Sanchez-Betancourt (2026) HJB.

Reference:
    Bouchard, B., Han, Y., Hu, R., Sanchez-Betancourt, L. (2026). "Trading in
    CEXs and DEXs with Priority Fees and Stochastic Delays." arXiv:2602.10798.

Setup:
    LP holds inventory q (in token0). The hedge venue (Extended perp / CEX) is
    accessed with random execution delay τ. Perp price has basis vs the
    AMM-implied price, modeled as an OU process with vol σ_basis and
    mean-reversion 1/τ_basis.

The LQ approximation gives the optimal hedge ratio ρ* in closed form. Two
relevant limits:
    - σ_basis → 0:  ρ* → 1   (full delta hedge — naive baseline)
    - σ_basis → ∞:  ρ* → 0   (don't hedge; basis dominates)

Closed-form result (LQ approximation under exponential-delay execution):

    ρ*(σ, σ_basis, τ, γ, κ) = (γ σ² T) / (γ σ² T + κ + γ σ_basis² τ)

where:
    σ        — annualized AMM-side vol
    σ_basis  — annualized basis (perp_price − amm_price) vol
    τ        — mean execution delay (years; e.g., 30s = 1e-6 yr)
    γ        — risk aversion (1/USD)
    κ        — quadratic transaction-cost coefficient (USD)
    T        — horizon (years)

This generalizes naively to multi-period: re-evaluate ρ* each step using
realized σ, σ_basis, and current state.
"""

from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class HedgeParams:
    sigma: float            # AMM-side annualized vol
    sigma_basis: float      # basis annualized vol
    delay_years: float      # mean execution delay (years)
    risk_aversion: float = 1.0
    txn_cost_coef: float = 0.0
    horizon_years: float = 1 / 365.0  # default 1-day horizon


def optimal_hedge_ratio(p: HedgeParams) -> float:
    """LQ-optimal hedge ratio ρ* ∈ [0, 1]; naive full-Δ = 1.0."""
    if p.sigma <= 0:
        return 0.0
    num = p.risk_aversion * p.sigma ** 2 * p.horizon_years
    den = num + p.txn_cost_coef + p.risk_aversion * p.sigma_basis ** 2 * p.delay_years
    if den <= 0:
        return 1.0
    rho = num / den
    return max(0.0, min(1.0, float(rho)))


def hedge_size_token0(delta_lp_token0: float, p: HedgeParams) -> float:
    """Optimal hedge size in token0. Negative = short the perp."""
    return -optimal_hedge_ratio(p) * delta_lp_token0


def avellaneda_stoikov_skew(q: float, sigma: float, gamma: float, T_minus_t: float) -> float:
    """Inventory-skew quote shift à la Avellaneda-Stoikov (2008).

    For a market-maker quoting against the LP/perp pair with inventory q:
        reservation price r = mid - q · γ · σ² · (T-t)
    The MM quotes around r, not mid; bid = r - δ_b, ask = r + δ_a.

    This is the natural companion to the LQ hedge — when running BOTH a hedge
    and an MM book, this is the inventory penalty applied to quotes.
    """
    return -q * gamma * sigma ** 2 * T_minus_t


def funding_aware_hedge_ratio(p: HedgeParams, funding_rate: float) -> float:
    """Adjust hedge ratio for funding rate.

    Observation: when funding pays the short (positive funding from the perp
    convention), under-hedging trades funding income for delta risk. When
    funding is paid by the short, the optimum tilts toward less hedge.

    Heuristic adjustment under LQ:
        ρ_adj = ρ_LQ × (1 - funding / σ²)        bounded to [0, 1]

    This matches the LQ first-order condition when funding enters the
    running cost linearly in the hedge size.
    """
    rho = optimal_hedge_ratio(p)
    if p.sigma <= 0:
        return rho
    adj = rho * (1.0 - funding_rate / max(p.sigma ** 2, 1e-9))
    return max(0.0, min(1.0, float(adj)))
