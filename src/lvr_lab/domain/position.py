"""
Position — a single concentrated-liquidity position on Ekubo / Uniswap v3.

Pure value object. No I/O, no math beyond what's needed for invariants.
The math kernels (compute/greeks.py, compute/lvr.py) operate on these
positions. Don't put math here; put accessors and invariants.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .types import (
    Liquidity, Price, Tick, Address, PoolId, BlockNumber,
    price_to_tick, tick_to_price,
)


class PositionState(Enum):
    """Where the position sits relative to the current pool price."""
    BELOW_RANGE = "below_range"   # p < p_a  → all token0
    IN_RANGE = "in_range"         # p_a ≤ p ≤ p_b → both tokens
    ABOVE_RANGE = "above_range"   # p > p_b  → all token1


def in_range_status(p: Price, p_a: Price, p_b: Price) -> PositionState:
    """Classify a position by current price."""
    if p < p_a:
        return PositionState.BELOW_RANGE
    if p > p_b:
        return PositionState.ABOVE_RANGE
    return PositionState.IN_RANGE


@dataclass(frozen=True)
class Position:
    """A v3-style concentrated-liquidity position.

    Required:
        L:    liquidity invariant (positive)
        p_a:  lower price bound (token1/token0, positive)
        p_b:  upper price bound (positive, > p_a)

    Optional metadata (for indexer reconstruction / cohort tagging):
        owner:        address that opened the position
        pool_id:      Ekubo pool key
        opened_block: block at which the position was first observed
        opened_price: pool price at open (for IL accounting)
        nft_id:       Ekubo position NFT id (for ownership tracking)
    """
    L: Liquidity
    p_a: Price
    p_b: Price

    # Metadata — defaults preserve backwards compatibility with the simple
    # 3-arg constructor used in the original compute/greeks.py.
    owner: Optional[Address] = None
    pool_id: Optional[PoolId] = None
    opened_block: Optional[BlockNumber] = None
    opened_price: Optional[Price] = None
    nft_id: Optional[int] = None
    fee_tier_bps: Optional[float] = None

    def __post_init__(self):
        if not (self.p_a > 0 and self.p_b > self.p_a):
            raise ValueError(
                f"require 0 < p_a < p_b; got p_a={self.p_a}, p_b={self.p_b}"
            )
        if self.L <= 0:
            raise ValueError(f"require L > 0; got L={self.L}")

    # ---------- Geometry ----------
    @property
    def width_pct(self) -> float:
        """Half-width of the range as a fraction of the geometric mean.

        Uniform-band ±5% → width_pct ≈ 0.05.
        """
        center = math.sqrt(self.p_a * self.p_b)
        return (math.sqrt(self.p_b) - math.sqrt(self.p_a)) / math.sqrt(center) / 2.0

    @property
    def geometric_mean_price(self) -> Price:
        return math.sqrt(self.p_a * self.p_b)

    def in_range(self, p: Price) -> bool:
        return self.p_a < p < self.p_b

    def status(self, p: Price) -> PositionState:
        return in_range_status(p, self.p_a, self.p_b)

    # ---------- Tick conversions (v3-style; Ekubo uses same convention) ----------
    @property
    def tick_lower(self) -> Tick:
        return price_to_tick(self.p_a)

    @property
    def tick_upper(self) -> Tick:
        return price_to_tick(self.p_b)

    def width_ticks(self) -> int:
        return self.tick_upper - self.tick_lower

    # ---------- Capital snapshot ----------
    def capital_required_at_open(self, opened_price: Optional[Price] = None) -> float:
        """USD-equivalent capital this position consumed at open, if known.

        Requires `opened_price` either as arg or via the dataclass field.
        Returns NaN if neither is set (caller must handle).
        """
        p_open = opened_price or self.opened_price
        if p_open is None:
            return float("nan")
        return _value_in_token1(self.L, self.p_a, self.p_b, p_open)

    # ---------- Equality and string repr ----------
    def __repr__(self) -> str:
        return (
            f"Position(L={self.L:.4g}, p_a={self.p_a:.4g}, p_b={self.p_b:.4g}"
            + (f", nft={self.nft_id}" if self.nft_id is not None else "")
            + ")"
        )

    def to_dict(self) -> dict:
        """Serialize to a dict; useful for events / persistence layers."""
        return {
            "L": self.L,
            "p_a": self.p_a,
            "p_b": self.p_b,
            "owner": self.owner,
            "pool_id": self.pool_id,
            "opened_block": self.opened_block,
            "opened_price": self.opened_price,
            "nft_id": self.nft_id,
            "fee_tier_bps": self.fee_tier_bps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        """Deserialize from a dict; missing optional fields → None."""
        return cls(
            L=d["L"],
            p_a=d["p_a"],
            p_b=d["p_b"],
            owner=d.get("owner"),
            pool_id=d.get("pool_id"),
            opened_block=d.get("opened_block"),
            opened_price=d.get("opened_price"),
            nft_id=d.get("nft_id"),
            fee_tier_bps=d.get("fee_tier_bps"),
        )


# ---------- Internal value helper (kept here so the dataclass is self-sufficient) ----------
def _value_in_token1(L: Liquidity, p_a: Price, p_b: Price, p: Price) -> float:
    """Value of an LP position in token1 numéraire — duplicates compute/greeks
    intentionally to avoid a circular dep when domain/ is being restructured.
    The compute version is the canonical one for production callers.
    """
    sqrt_p = math.sqrt(p)
    sqrt_pa = math.sqrt(p_a)
    sqrt_pb = math.sqrt(p_b)
    if p <= p_a:
        x = L * (1 / sqrt_pa - 1 / sqrt_pb)
        return x * p
    if p >= p_b:
        return L * (sqrt_pb - sqrt_pa)
    x = L * (1 / sqrt_p - 1 / sqrt_pb)
    y = L * (sqrt_p - sqrt_pa)
    return y + x * p
