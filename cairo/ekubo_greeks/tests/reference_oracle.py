"""
Reference oracle for the Cairo `ekubo-greeks` library.

Property-test pattern (run with `pytest tests/property/`):
    1. Generate random valid positions (L, p_a, p_b)
    2. Compute Greeks via Python reference (compute/greeks.py)
    3. Compute Greeks via Cairo (via starknet-py call to a deployed test contract)
    4. Assert |python − cairo| / max(|python|, ε) < REL_TOL

REL_TOL = 1e-15 for normal inputs (single-felt 18-dec FP precision).
REL_TOL = 1e-9  for extreme inputs (very small/large prices) where u256_sqrt
                 loses ULPs.

This file is the *spec* — the actual deployed test runner lives at
`tests/property/test_cairo_vs_python.py` and uses starknet-py to invoke
the Cairo functions on a forked devnet.
"""
from __future__ import annotations
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from lvr_lab.compute import (
    Position, position_amounts, position_value, delta, gamma,
    impermanent_loss, marginal_liquidity, lvr_rate_in_range,
    sigma_fee_closed_form,
)


def to_18dec(x: float) -> int:
    """Float → 18-decimal fixed-point u256 representation."""
    return int(x * 10**18)


def from_18dec(x: int) -> float:
    """18-decimal fixed-point u256 → float."""
    return x / 10**18


# ---------- Python reference computations matching Cairo signatures ----------
def py_position_amounts(L: float, p_a: float, p_b: float, p: float):
    """Mirrors Cairo `position::position_amounts`."""
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    x, y = position_amounts(pos, p)
    return x, y


def py_position_value(L: float, p_a: float, p_b: float, p: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return position_value(pos, p)


def py_delta_token0(L: float, p_a: float, p_b: float, p: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return delta(pos, p)


def py_gamma_abs(L: float, p_a: float, p_b: float, p: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return abs(gamma(pos, p))


def py_speed_abs(L: float, p_a: float, p_b: float, p: float) -> float:
    """|∂³V/∂p³| = 3L / (4 p^{5/2}) for in-range; 0 outside."""
    if p <= p_a or p >= p_b:
        return 0.0
    return 3.0 * L / (4.0 * p**2.5)


def py_lvr_vega(L: float, p_a: float, p_b: float, p: float, sigma: float) -> float:
    """σ · L · √p / 2 in-range; 0 outside."""
    if p <= p_a or p >= p_b:
        return 0.0
    return sigma * L * math.sqrt(p) / 2.0


def py_dollar_gamma(L: float, p_a: float, p_b: float, p: float) -> float:
    """0.5 · |Γ| · p² for in-range; 0 outside."""
    if p <= p_a or p >= p_b:
        return 0.0
    return 0.5 * (L / (2 * p**1.5)) * (p**2)


def py_marginal_liquidity(L: float, p_a: float, p_b: float, p: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return marginal_liquidity(pos, p)


def py_lvr_rate(L: float, p_a: float, p_b: float, p: float, sigma: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return lvr_rate_in_range(pos, p, sigma)


def py_sigma_fee(fees: float, L: float, p: float, dt_seconds: float) -> float:
    return sigma_fee_closed_form(fees, L, p, dt_seconds, annualized=True)


def py_il_vs_hodl(L: float, p_a: float, p_b: float, p_t: float, p_0: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return abs(impermanent_loss(pos, p_t, p_0))


def py_il_vs_passive(L: float, p_a: float, p_b: float, p_t: float, p_0: float) -> float:
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    return abs(position_value(pos, p_t) - position_value(pos, p_0))


def py_capital_efficiency(L: float, p_a: float, p_b: float, p_ref: float) -> float:
    if not (p_a < p_ref < p_b):
        return 0.0
    coeff = 2 * math.sqrt(p_ref) - math.sqrt(p_a) - p_ref / math.sqrt(p_b)
    return L / coeff if coeff > 0 else 0.0


# ---------- Sanity smoke test ----------
if __name__ == "__main__":
    L, p_a, p_b, p = 100.0, 1500.0, 2500.0, 2000.0
    print(f"Reference oracle smoke test (L={L}, p_a={p_a}, p_b={p_b}, p={p})")
    print(f"  amounts:        {py_position_amounts(L, p_a, p_b, p)}")
    print(f"  value:          {py_position_value(L, p_a, p_b, p):.6f}")
    print(f"  delta_token0:   {py_delta_token0(L, p_a, p_b, p):.6f}")
    print(f"  gamma_abs:      {py_gamma_abs(L, p_a, p_b, p):.6f}")
    print(f"  speed_abs:      {py_speed_abs(L, p_a, p_b, p):.6f}")
    print(f"  lvr_rate σ=0.5: {py_lvr_rate(L, p_a, p_b, p, 0.5):.6f}")
    print(f"  marginal_liq:   {py_marginal_liquidity(L, p_a, p_b, p):.6f}")
    print(f"  capital_eff:    {py_capital_efficiency(L, p_a, p_b, p):.6f}")
    print()
    print("Cairo equivalents to be cross-checked via starknet-py invocation in")
    print("tests/property/test_cairo_vs_python.py (requires scarb + starknet-devnet).")
