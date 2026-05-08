"""
Cross-AMM unified pool object — common interface across Ekubo, Uniswap v3, and
Trader Joe LB so that wedge / σ_fee comparisons are apples-to-apples.

The PoolPanel object holds (date, fee_yield, σ_realized, wedge, n_swaps, TVL,
incentive_density) and exposes summary statistics with bootstrap CIs.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import List, Sequence, Optional

from ..compute.bootstrap import block_bootstrap_ci


@dataclass
class PoolDay:
    date: str               # ISO date
    pool: str               # 'ekubo:USDC/STRK', 'lb:WAVAX/USDC 20bp', etc.
    amm: str                # 'ekubo' | 'v3' | 'lb'
    fee_yield_ann: float    # annualized fees / TVL
    sigma_realized: float   # annualized realized vol
    n_swaps: int
    tvl_usd: float
    incentive_usd_ann: float = 0.0
    fee_tier_bps: Optional[float] = None

    @property
    def wedge(self) -> float:
        return self.fee_yield_ann - self.sigma_realized

    @property
    def sigma_fee_proxy(self) -> float:
        """σ_fee = 2·sqrt(fee_yield_ann) (TVL-proxy, see compute/sigma_fee.py)."""
        return 2.0 * np.sqrt(max(self.fee_yield_ann, 0.0))


@dataclass
class PoolPanel:
    rows: List[PoolDay]

    def by_amm(self, amm: str) -> "PoolPanel":
        return PoolPanel([r for r in self.rows if r.amm == amm])

    def by_pool(self, pool: str) -> "PoolPanel":
        return PoolPanel([r for r in self.rows if r.pool == pool])

    def wedge_series(self) -> np.ndarray:
        return np.array([r.wedge for r in self.rows], dtype=float)

    def fee_yield_series(self) -> np.ndarray:
        return np.array([r.fee_yield_ann for r in self.rows], dtype=float)

    def sigma_realized_series(self) -> np.ndarray:
        return np.array([r.sigma_realized for r in self.rows], dtype=float)


def wedge_summary(panel: PoolPanel, confidence: float = 0.95,
                  n_resamples: int = 5000) -> dict:
    """Bootstrap CI on the median wedge over a panel."""
    s = panel.wedge_series()
    point, lo, hi = block_bootstrap_ci(
        s, statistic=np.median, n_resamples=n_resamples, confidence=confidence
    )
    return {
        "median_wedge": point,
        "ci_low": lo,
        "ci_high": hi,
        "n_pool_days": len(s),
        "confidence": confidence,
    }
