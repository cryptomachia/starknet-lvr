"""
P&L attribution — decompose realized vault P&L into its sources.

Standard attribution for a delta-neutral LP vault:

  PnL_total = ΔV_LP + Fees − LVR + Hedge_PnL − Funding − Slippage − Gas

Each component answers a different question:
  - ΔV_LP:     LP value change (price-driven, IL-included)
  - Fees:      revenue from swap volume
  - LVR:       theoretical (informed-flow) loss to arbitrageurs
  - Hedge_PnL: realized perp PnL
  - Funding:   perp funding paid (or received)
  - Slippage:  realized vs theoretical execution price
  - Gas:       chain transaction cost

The empirical residual (PnL_total − sum of components) tells us how good
our model is. Tight residual → model captures the dynamics. Wide residual
→ unmodeled friction (basis risk, JIT, oracle lag).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class AttributionRow:
    """One source contribution to total PnL."""
    source: str
    value: float
    pct_of_total: float


@dataclass
class AttributionReport:
    """Full P&L attribution decomposition."""
    total_pnl: float
    rows: list[AttributionRow]
    residual: float
    residual_pct: float

    def __str__(self) -> str:
        lines = ["P&L attribution:"]
        for r in self.rows:
            sign = "+" if r.value >= 0 else "−"
            lines.append(f"  {r.source:<14} {sign}${abs(r.value):>10,.2f}  ({r.pct_of_total:+.1f}%)")
        lines.append(f"  {'residual':<14} {'+' if self.residual >= 0 else '−'}${abs(self.residual):>10,.2f}  ({self.residual_pct:+.1f}%)")
        lines.append(f"  {'=' * 30}")
        sign = "+" if self.total_pnl >= 0 else "−"
        lines.append(f"  {'TOTAL':<14} {sign}${abs(self.total_pnl):>10,.2f}")
        return "\n".join(lines)


def attribute_pnl(
    total_pnl: float,
    delta_v_lp: float,
    fees: float,
    lvr: float,
    hedge_pnl: float = 0.0,
    funding: float = 0.0,
    slippage: float = 0.0,
    gas: float = 0.0,
) -> AttributionReport:
    """Decompose a vault's realized P&L into its sources.

    Sign convention for inputs:
      - delta_v_lp:  signed (positive if LP appreciated)
      - fees:        positive (revenue)
      - lvr:         positive (cost; we'll negate it)
      - hedge_pnl:   signed
      - funding:     positive (cost; we'll negate it)
      - slippage:    positive (cost; we'll negate it)
      - gas:         positive (cost; we'll negate it)
    """
    components = {
        "ΔV_LP": delta_v_lp,
        "fees": fees,
        "LVR": -lvr,
        "hedge_PnL": hedge_pnl,
        "funding": -funding,
        "slippage": -slippage,
        "gas": -gas,
    }
    explained = sum(components.values())
    residual = total_pnl - explained
    rows = [
        AttributionRow(
            source=name,
            value=val,
            pct_of_total=(val / abs(total_pnl) * 100) if total_pnl != 0 else 0.0,
        )
        for name, val in components.items()
        if val != 0
    ]
    return AttributionReport(
        total_pnl=total_pnl,
        rows=rows,
        residual=residual,
        residual_pct=(residual / abs(total_pnl) * 100) if total_pnl != 0 else 0.0,
    )


def factor_attribution(returns_matrix: np.ndarray, factor_loadings: np.ndarray) -> dict:
    """Decompose realized returns into factor contributions.

    Args:
        returns_matrix:  (T × N) — T periods, N positions.
        factor_loadings: (N × K) — each column is a factor (e.g., from PCA).

    Returns:
        per-factor cumulative contribution to total return + idiosyncratic.
    """
    R = np.asarray(returns_matrix, dtype=float)
    L = np.asarray(factor_loadings, dtype=float)
    if R.ndim != 2 or L.ndim != 2:
        raise ValueError("inputs must be 2D")
    T, N = R.shape
    if L.shape[0] != N:
        raise ValueError(f"loadings rows {L.shape[0]} != returns cols {N}")

    # Factor returns: T × K
    F = R @ L
    # Factor contributions: weight × factor return
    K = L.shape[1]
    explained_returns = F @ L.T   # T × N
    idiosyncratic = R - explained_returns
    return {
        "factor_returns_T_x_K": F,
        "explained_T_x_N": explained_returns,
        "idiosyncratic_T_x_N": idiosyncratic,
        "total_explained_variance": float(explained_returns.var() / R.var()) if R.var() > 0 else 0.0,
        "total_idiosyncratic_variance": float(idiosyncratic.var() / R.var()) if R.var() > 0 else 0.0,
    }
