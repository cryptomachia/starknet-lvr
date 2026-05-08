"""
Domain layer — pure value objects and domain types.

The foundation everything else depends on:
- compute/    depends on domain/
- analysis/   depends on domain/ (and compute/)
- indexer/    depends on domain/ (and infrastructure/)
- api/        depends on domain/ (and application/)

Nothing in this package may import from compute/, analysis/, api/, or any
external service. Everything here is pure: value objects, type aliases,
domain events, and small algebra.

Test invariant: a snapshot of `pip freeze` for this package's dependencies
should contain ONLY {dataclasses, typing, enum, math, decimal} — Python
standard library. No numpy, no pandas, no scipy.
"""

from .types import (
    Felt252,
    PoolId,
    BlockNumber,
    Timestamp,
    Wei,
    Price,
    Liquidity,
    Tick,
    Address,
    Decimals,
    Bps,
    short_string_to_felt,
    felt_to_short_string,
    hex_to_felt,
    felt_to_hex,
    from_wei,
    to_wei,
    usd_value,
    bps_to_fraction,
    fraction_to_bps,
    tick_to_price,
    price_to_tick,
    nearest_tick,
)
from .position import Position, PositionState, in_range_status
from .pool import Pool, PoolKey, FeeTier, AmmFamily
from .swap import Swap, SwapDirection
from .profile import (
    LpProfile,
    UniformBandProfile,
    ConcentratedActiveProfile,
    OnchainReconstructedProfile,
    PiecewiseProfile,
)
from .portfolio import Portfolio, PortfolioGreeks
from .events import (
    DomainEvent,
    SwapEvent,
    PositionOpenedEvent,
    PositionUpdatedEvent,
    PositionClosedEvent,
    OracleUpdateEvent,
    GaugeRateAdjustedEvent,
    ExtensionStateChangedEvent,
)

__all__ = [
    # types
    "Felt252", "PoolId", "BlockNumber", "Timestamp", "Wei", "Price",
    "Liquidity", "Tick", "Address", "Decimals", "Bps",
    "short_string_to_felt", "felt_to_short_string",
    "hex_to_felt", "felt_to_hex", "from_wei", "to_wei", "usd_value",
    "bps_to_fraction", "fraction_to_bps",
    "tick_to_price", "price_to_tick", "nearest_tick",
    # position
    "Position", "PositionState", "in_range_status",
    # pool
    "Pool", "PoolKey", "FeeTier", "AmmFamily",
    # swap
    "Swap", "SwapDirection",
    # profiles
    "LpProfile", "UniformBandProfile", "ConcentratedActiveProfile",
    "OnchainReconstructedProfile", "PiecewiseProfile",
    # portfolio
    "Portfolio", "PortfolioGreeks",
    # events
    "DomainEvent", "SwapEvent", "PositionOpenedEvent", "PositionUpdatedEvent",
    "PositionClosedEvent", "OracleUpdateEvent", "GaugeRateAdjustedEvent",
    "ExtensionStateChangedEvent",
]
