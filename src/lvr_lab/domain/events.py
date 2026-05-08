"""
Domain events — what an indexer publishes; what compute and dashboard subscribe to.

Each event is a frozen dataclass with the same shape across producers.
The event bus implementation lives in `infrastructure/`; here we just
define the message types.

Convention:
- Each event carries (block_number, block_timestamp, tx_hash) for replay.
- Each event has an `event_type` string for routing.
- Domain events are *what happened*, not *what to do*. Side-effects live in
  application/.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, ClassVar

from .types import (
    Address, PoolId, BlockNumber, Timestamp, Wei, Price, Liquidity, Bps,
)
from .swap import Swap


@dataclass(frozen=True)
class DomainEvent:
    """Base event carrying common fields."""
    event_type: ClassVar[str] = "domain"

    block_number: BlockNumber
    block_timestamp: Timestamp
    tx_hash: str
    log_index: int = 0


@dataclass(frozen=True)
class SwapEvent(DomainEvent):
    """A swap was executed in a pool. Carries the full Swap object."""
    event_type: ClassVar[str] = "swap"
    swap: Swap = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PositionOpenedEvent(DomainEvent):
    """An LP opened a new position (Ekubo Position* event with positive liquidity)."""
    event_type: ClassVar[str] = "position_opened"

    pool_id: PoolId = ""
    nft_id: int = 0
    owner: Address = 0
    L: Liquidity = 0.0
    p_a: Price = 0.0
    p_b: Price = 0.0
    pool_price_at_open: Price = 0.0


@dataclass(frozen=True)
class PositionUpdatedEvent(DomainEvent):
    """An existing position's liquidity changed (deposit, withdraw, or fee collect)."""
    event_type: ClassVar[str] = "position_updated"

    pool_id: PoolId = ""
    nft_id: int = 0
    L_delta: Liquidity = 0.0       # signed: positive = deposit, negative = withdraw
    fees_collected_token0_wei: Wei = 0
    fees_collected_token1_wei: Wei = 0


@dataclass(frozen=True)
class PositionClosedEvent(DomainEvent):
    """Position fully withdrawn (L → 0)."""
    event_type: ClassVar[str] = "position_closed"

    pool_id: PoolId = ""
    nft_id: int = 0


@dataclass(frozen=True)
class OracleUpdateEvent(DomainEvent):
    """A reference oracle pushed a new price.

    Used by the basis-tracking pipeline (compare Pragma vs CEX).
    """
    event_type: ClassVar[str] = "oracle_update"

    oracle: Address = 0       # which oracle: Pragma, Chainlink, etc.
    pair_id: int = 0          # encoded short-string felt
    price: Price = 0.0
    decimals: int = 8
    n_sources_aggregated: int = 0


@dataclass(frozen=True)
class GaugeRateAdjustedEvent(DomainEvent):
    """DeFi Spring rate adjustment — the H3 IV instrument."""
    event_type: ClassVar[str] = "gauge_rate_adjusted"

    distributor: Address = 0
    new_rate_per_second: float = 0.0
    old_rate_per_second: float = 0.0
    epoch: int = 0


@dataclass(frozen=True)
class ExtensionStateChangedEvent(DomainEvent):
    """A dynamic-fee extension's internal state changed.

    Important for σ_fee computation on extension-pools — the σ_fee fixed
    point depends on extension state φ.
    """
    event_type: ClassVar[str] = "extension_state_changed"

    pool_id: PoolId = ""
    extension: Address = 0
    state_blob: bytes = b""    # opaque; consumer-specific decoding
    fee_bps_effective: Optional[Bps] = None
