"""
Swap — a single trade event on an AMM.

The atomic unit the indexer normalizes into. Markouts, LVR, and fee-attribution
all operate on streams of Swaps.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .types import (
    PoolId, Address, BlockNumber, Timestamp, Wei, Price,
    from_wei,
)


class SwapDirection(Enum):
    """Which way the trader went. Token0→Token1 means the trader sold token0."""
    TOKEN0_TO_TOKEN1 = "0_to_1"   # trader sells token0; pool price decreases
    TOKEN1_TO_TOKEN0 = "1_to_0"   # trader sells token1; pool price increases


@dataclass(frozen=True)
class Swap:
    """A single swap event on Ekubo / v3 / LB.

    Normalized so all AMM families share the same shape.
    """
    pool_id: PoolId
    direction: SwapDirection

    amount0_in_wei: Wei                    # signed by direction at the indexer level
    amount1_in_wei: Wei
    fee_amount_wei: Wei                    # in numéraire of token-paid (depends on direction)
    fee_token_is_0: bool                   # convention: which side the fee was charged in

    pool_price_pre: Price                  # AMM price right before the swap
    pool_price_post: Price                 # AMM price right after
    pool_tick_pre: int
    pool_tick_post: int

    block_number: BlockNumber
    block_timestamp: Timestamp
    tx_hash: str
    log_index: int                         # for ordering within a block

    trader: Optional[Address] = None       # if known
    extension_state_pre: Optional[bytes] = None   # for dynamic-fee pools

    # ---------- Convenience derived properties ----------
    def amount0_in(self, decimals: int) -> float:
        return from_wei(self.amount0_in_wei, decimals)

    def amount1_in(self, decimals: int) -> float:
        return from_wei(self.amount1_in_wei, decimals)

    def fee_amount(self, fee_decimals: int) -> float:
        return from_wei(self.fee_amount_wei, fee_decimals)

    def crosses_tick(self) -> bool:
        return self.pool_tick_pre != self.pool_tick_post

    def is_buy_pressure(self) -> bool:
        """True if this swap pushed pool price UP (token1→token0 direction)."""
        return self.pool_price_post > self.pool_price_pre

    @property
    def signed_amount0(self) -> int:
        """Positive = pool sold token0 to trader; negative = pool bought token0."""
        if self.direction == SwapDirection.TOKEN0_TO_TOKEN1:
            return -self.amount0_in_wei  # trader brought token0 in → pool's amt0 went up → "negative for the LP's net pos change"
        return self.amount0_in_wei

    def __repr__(self) -> str:
        arrow = "0→1" if self.direction == SwapDirection.TOKEN0_TO_TOKEN1 else "1→0"
        return (
            f"Swap(pool={self.pool_id[:10]}.. {arrow} "
            f"price {self.pool_price_pre:.4g}→{self.pool_price_post:.4g} "
            f"@blk {self.block_number})"
        )
