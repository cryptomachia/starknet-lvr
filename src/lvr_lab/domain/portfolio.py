"""
Portfolio — collection of Positions with aggregate Greeks.

A portfolio is the right abstraction whenever we have multiple positions:
- A vault holding several LP ranges
- An LP analyzing all their positions across pools
- Cross-pool risk aggregation

Aggregate Greeks are linear over positions (Δ, Γ all sum), but only when
all positions reference the same price. For multi-pool portfolios the
caller must group by token-pair.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Iterable, Optional

from .position import Position, PositionState
from .types import Price, Liquidity


@dataclass(frozen=True)
class PortfolioGreeks:
    """Aggregate Greeks at a single price."""
    value_token1: float
    delta_token0: float
    gamma: float
    n_positions_in_range: int
    n_positions_below: int
    n_positions_above: int


@dataclass
class Portfolio:
    """A collection of LP positions on the same token pair.

    For cross-pair portfolios, hold multiple Portfolio objects keyed by pair.
    Mutability is intentional — positions are added/removed at runtime by
    backtest engines and indexers.
    """
    positions: List[Position] = field(default_factory=list)

    # ---------- Mutators ----------
    def add(self, pos: Position) -> None:
        self.positions.append(pos)

    def remove_by_nft(self, nft_id: int) -> Optional[Position]:
        for i, pos in enumerate(self.positions):
            if pos.nft_id == nft_id:
                return self.positions.pop(i)
        return None

    def __len__(self) -> int:
        return len(self.positions)

    def __iter__(self) -> Iterable[Position]:
        return iter(self.positions)

    # ---------- Aggregate Greeks (linear over positions) ----------
    def aggregate_greeks(self, p: Price) -> PortfolioGreeks:
        """Sum Δ, Γ across all positions at price p.

        Each position contributes:
          x(p)  = inventory in token0          → contributes to delta
          V(p)  = position value in token1
          Γ(p)  = -L / (2 p^{3/2}) when in-range

        Aggregate is just the sum (linearity of derivatives).
        """
        if not self.positions:
            return PortfolioGreeks(0.0, 0.0, 0.0, 0, 0, 0)

        total_value = 0.0
        total_delta = 0.0
        total_gamma = 0.0
        n_in = n_below = n_above = 0

        sqrt_p = math.sqrt(p)

        for pos in self.positions:
            sqrt_pa = math.sqrt(pos.p_a)
            sqrt_pb = math.sqrt(pos.p_b)
            if p <= pos.p_a:
                x = pos.L * (1.0 / sqrt_pa - 1.0 / sqrt_pb)
                y = 0.0
                gamma = 0.0
                n_below += 1
            elif p >= pos.p_b:
                x = 0.0
                y = pos.L * (sqrt_pb - sqrt_pa)
                gamma = 0.0
                n_above += 1
            else:
                x = pos.L * (1.0 / sqrt_p - 1.0 / sqrt_pb)
                y = pos.L * (sqrt_p - sqrt_pa)
                gamma = -pos.L / (2.0 * p ** 1.5)
                n_in += 1
            total_value += y + x * p
            total_delta += x
            total_gamma += gamma

        return PortfolioGreeks(
            value_token1=total_value,
            delta_token0=total_delta,
            gamma=total_gamma,
            n_positions_in_range=n_in,
            n_positions_below=n_below,
            n_positions_above=n_above,
        )

    # ---------- Subsetting ----------
    def in_range_at(self, p: Price) -> "Portfolio":
        """Return a sub-portfolio of positions in-range at price p."""
        return Portfolio([pos for pos in self.positions if pos.in_range(p)])

    def by_owner(self, owner_address: int) -> "Portfolio":
        return Portfolio([pos for pos in self.positions if pos.owner == owner_address])

    def by_pool(self, pool_id) -> "Portfolio":
        return Portfolio([pos for pos in self.positions if pos.pool_id == pool_id])

    # ---------- Aggregate stats ----------
    @property
    def total_liquidity(self) -> Liquidity:
        return sum(pos.L for pos in self.positions)

    def liquidity_in_range(self, p: Price) -> Liquidity:
        return sum(pos.L for pos in self.positions if pos.in_range(p))

    def coverage_min_price(self) -> Optional[Price]:
        """Smallest p_a across all positions."""
        if not self.positions:
            return None
        return min(pos.p_a for pos in self.positions)

    def coverage_max_price(self) -> Optional[Price]:
        if not self.positions:
            return None
        return max(pos.p_b for pos in self.positions)
