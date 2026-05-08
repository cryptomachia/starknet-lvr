"""
LP profile types — strategies for how to spread liquidity over a price range.

A profile knows how to convert (target capital, current price) → Position.
The math kernels operate on Positions; profiles are the *bridge* from
strategy to concrete position.
"""

from __future__ import annotations
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List

from .position import Position
from .types import Liquidity, Price


class LpProfile(ABC):
    """Abstract base for LP-position-construction strategies."""

    @abstractmethod
    def build(
        self,
        capital_token1: float,
        current_price: Price,
        opened_block: Optional[int] = None,
    ) -> Position:
        """Construct a Position consuming `capital_token1` USD-equivalent at `current_price`."""
        ...

    @abstractmethod
    def name(self) -> str: ...


@dataclass(frozen=True)
class UniformBandProfile(LpProfile):
    """Default Ekubo UI preset: liquidity uniform over [p·(1−w), p·(1+w)].

    Used as the canonical baseline. Fee earnings are spread thin; LVR per
    crossing is small but crossings happen often.
    """
    width_pct: float     # half-width as fraction (0.05 = ±5%)

    def build(self, capital_token1: float, current_price: Price,
              opened_block: Optional[int] = None) -> Position:
        if not 0 < self.width_pct < 1:
            raise ValueError(f"width_pct must be in (0, 1); got {self.width_pct}")
        p_a = current_price * (1.0 - self.width_pct)
        p_b = current_price * (1.0 + self.width_pct)
        coeff = (
            2 * math.sqrt(current_price)
            - math.sqrt(p_a)
            - current_price / math.sqrt(p_b)
        )
        if coeff <= 0:
            raise ValueError("degenerate uniform band — coefficient ≤ 0")
        L = capital_token1 / coeff
        return Position(
            L=L, p_a=p_a, p_b=p_b,
            opened_block=opened_block,
            opened_price=current_price,
        )

    def name(self) -> str:
        return f"uniform-band ±{self.width_pct*100:.1f}%"


@dataclass(frozen=True)
class ConcentratedActiveProfile(LpProfile):
    """Mass piled near current price.

    Implementation: a tighter uniform band whose half-width is the desired
    `effective_concentration` parameter. concentration=10× tighter than
    UniformBand(0.05) → width_pct = 0.005.
    """
    half_width_pct: float    # e.g., 0.005 = ±0.5%

    def build(self, capital_token1: float, current_price: Price,
              opened_block: Optional[int] = None) -> Position:
        if not 0 < self.half_width_pct < 1:
            raise ValueError(f"half_width_pct must be in (0, 1); got {self.half_width_pct}")
        p_a = current_price * (1.0 - self.half_width_pct)
        p_b = current_price * (1.0 + self.half_width_pct)
        coeff = (
            2 * math.sqrt(current_price)
            - math.sqrt(p_a)
            - current_price / math.sqrt(p_b)
        )
        if coeff <= 0:
            raise ValueError("degenerate concentrated band")
        L = capital_token1 / coeff
        return Position(
            L=L, p_a=p_a, p_b=p_b,
            opened_block=opened_block,
            opened_price=current_price,
        )

    def name(self) -> str:
        return f"concentrated ±{self.half_width_pct*100:.2f}%"


@dataclass(frozen=True)
class OnchainReconstructedProfile(LpProfile):
    """Reconstructed from observed on-chain `Position*` events.

    The indexer materializes a sequence of (block, L, p_a, p_b) tuples for each
    NFT id; this profile wraps the most recent observation.
    """
    L: Liquidity
    p_a: Price
    p_b: Price
    nft_id: Optional[int] = None

    def build(self, capital_token1: float, current_price: Price,
              opened_block: Optional[int] = None) -> Position:
        # capital_token1 is informational here — the position is fixed by L.
        return Position(
            L=self.L, p_a=self.p_a, p_b=self.p_b,
            opened_block=opened_block,
            opened_price=current_price,
            nft_id=self.nft_id,
        )

    def name(self) -> str:
        return f"on-chain reconstructed (nft={self.nft_id})"


# ---------- Multi-band / piecewise profile (for institutional LPs) ----------
@dataclass(frozen=True)
class PiecewiseProfile(LpProfile):
    """Multiple overlapping ranges with weights — the "hedgehog" pattern.

    Capital is allocated across ranges by `weights`; sum to 1. Result is a
    portfolio of Positions, but `build` returns a single representative
    Position (the one with the largest L) for backwards compat. Use
    `build_all` to get the full list.
    """
    ranges: List[tuple[float, float]]    # list of (lo_pct, hi_pct) relative to current
    weights: List[float]

    def __post_init__(self):
        if len(self.ranges) != len(self.weights):
            raise ValueError("ranges and weights length mismatch")
        if abs(sum(self.weights) - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1; got {sum(self.weights)}")

    def build(self, capital_token1: float, current_price: Price,
              opened_block: Optional[int] = None) -> Position:
        positions = self.build_all(capital_token1, current_price, opened_block)
        return max(positions, key=lambda p: p.L)

    def build_all(self, capital_token1: float, current_price: Price,
                  opened_block: Optional[int] = None) -> List[Position]:
        out = []
        for (lo, hi), w in zip(self.ranges, self.weights):
            if not (0 < lo < hi):
                raise ValueError(f"invalid range: ({lo}, {hi})")
            p_a = current_price * lo
            p_b = current_price * hi
            coeff = (
                2 * math.sqrt(current_price)
                - math.sqrt(p_a)
                - current_price / math.sqrt(p_b)
            )
            if coeff <= 0:
                continue
            L = (w * capital_token1) / coeff
            out.append(Position(
                L=L, p_a=p_a, p_b=p_b,
                opened_block=opened_block,
                opened_price=current_price,
            ))
        return out

    def name(self) -> str:
        return f"piecewise ({len(self.ranges)} bands)"
