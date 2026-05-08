"""
Pool — a tradeable pair on an AMM. Identifier + design parameters.

The Pool object is what an indexer ingests; the math kernels operate on
positions inside the pool, never on the pool itself.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .types import Address, PoolId, Bps, BlockNumber, Liquidity, Price


class AmmFamily(Enum):
    """Which AMM design lineage a pool belongs to."""
    EKUBO = "ekubo"             # Cairo singleton + extensions, sub-bp ticks
    UNISWAP_V3 = "uniswap-v3"   # EVM, 1bp tick minimum
    UNISWAP_V4 = "uniswap-v4"   # EVM, hooks
    TRADER_JOE_LB = "lb"        # Avalanche bins, constant-sum within
    BALANCER_V3 = "balancer-v3"
    OTHER = "other"


@dataclass(frozen=True)
class FeeTier:
    """A pool's fee schedule. For constant-fee pools, dynamic_extension is None."""
    base_bps: Bps                                  # e.g., 5 = 5 bp
    dynamic_extension: Optional[Address] = None    # if non-None, fees vary with extension state
    max_bps: Optional[Bps] = None                  # cap for dynamic-fee extensions

    def is_dynamic(self) -> bool:
        return self.dynamic_extension is not None


@dataclass(frozen=True)
class PoolKey:
    """The struct that uniquely identifies an Ekubo pool.

    Mirrors Ekubo's on-chain PoolKey: (token0, token1, fee, tick_spacing, extension).
    Hashing this struct gives the pool_id used in the singleton.
    """
    token0: Address
    token1: Address
    fee_bps: Bps
    tick_spacing: int
    extension: Optional[Address] = None

    def normalized(self) -> "PoolKey":
        """Ensure token0 < token1 (Ekubo convention)."""
        if self.token0 < self.token1:
            return self
        return PoolKey(
            token0=self.token1, token1=self.token0,
            fee_bps=self.fee_bps, tick_spacing=self.tick_spacing,
            extension=self.extension,
        )


@dataclass(frozen=True)
class Pool:
    """A pool snapshot — the indexer-level value object.

    Contains identification + current state observed at a given block.
    Per-block updates produce a new Pool object (immutable).
    """
    pool_id: PoolId
    family: AmmFamily
    key: PoolKey

    # Token metadata
    token0_symbol: str
    token1_symbol: str
    token0_decimals: int
    token1_decimals: int

    # Current state
    current_price: Price                # token1 per token0
    current_tick: int
    total_liquidity_in_range: Liquidity
    tvl_token0_wei: int
    tvl_token1_wei: int

    # Indexing metadata
    last_observed_block: BlockNumber
    last_observed_ts: float

    # Optional usd valuations
    tvl_usd: Optional[float] = None
    token0_usd_price: Optional[float] = None
    token1_usd_price: Optional[float] = None

    # ---------- Convenience ----------
    @property
    def is_stable_stable(self) -> bool:
        stables = {"USDC", "USDT", "USDC.e", "DAI", "AUSD0", "CASH"}
        return self.token0_symbol in stables and self.token1_symbol in stables

    @property
    def has_extension(self) -> bool:
        return self.key.extension is not None

    def display_pair(self) -> str:
        return f"{self.token0_symbol}/{self.token1_symbol}"

    def label_with_fee(self) -> str:
        return f"{self.display_pair()} {int(self.key.fee_bps)}bp"
