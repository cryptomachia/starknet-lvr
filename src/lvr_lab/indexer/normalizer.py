"""
Event normalizer — converts raw chain events to canonical domain events.

Each AMM family has its own event schema (Ekubo's `Swapped`, Uniswap v3's
`Swap`, Trader Joe LB's `Swap`). The normalizer hides those differences
behind the unified `Swap` domain type.

Each normalizer takes a raw RPC event (with .keys, .data, .block_number, ...)
and returns a domain event (or raises NormalizationError if the event isn't
one we care about — e.g., a Position update logged on the same contract).
"""

from __future__ import annotations
from typing import Any, Optional

from ..domain import (
    Swap, SwapDirection, SwapEvent,
    PositionOpenedEvent, PositionUpdatedEvent, PositionClosedEvent,
    OracleUpdateEvent, GaugeRateAdjustedEvent,
)
from ..compute.selectors import selector_hex


class NormalizationError(Exception):
    """Raised when a raw event isn't recognizable / we can't decode it."""


# ---------- Ekubo Core event selectors ----------
# Observed live on Starknet mainnet:
#   0x157717768aca88da4ac4279765f09f4d0151823d573537fbbeb950cdbd9a870
#   This is the dominant event from Ekubo Core — appears on every swap.
EKUBO_SWAPPED_SELECTOR_OBSERVED = (
    "0x157717768aca88da4ac4279765f09f4d0151823d573537fbbeb950cdbd9a870"
)
# Fallback computed selectors (in case the Cairo event name changes)
EKUBO_SWAPPED_SELECTOR_NAMED = selector_hex("Swapped")
EKUBO_POSITION_UPDATED_SELECTOR = selector_hex("PositionUpdated")
EKUBO_POSITION_FEES_COLLECTED_SELECTOR = selector_hex("PositionFeesCollected")

EKUBO_SWAPPED_SELECTORS = {
    EKUBO_SWAPPED_SELECTOR_OBSERVED,
    EKUBO_SWAPPED_SELECTOR_NAMED,
}


def normalize_ekubo_event(raw: dict[str, Any]) -> Optional[Any]:
    """Map a raw Ekubo Core event to a domain event.

    Returns None if the event is uninteresting (not one of the tracked types).
    """
    keys = raw.get("keys", [])
    if not keys:
        return None
    selector = keys[0]
    try:
        if selector in EKUBO_SWAPPED_SELECTORS:
            return _normalize_ekubo_swapped(raw)
        if selector == EKUBO_POSITION_UPDATED_SELECTOR:
            return _normalize_ekubo_position_updated(raw)
        if selector == EKUBO_POSITION_FEES_COLLECTED_SELECTOR:
            return _normalize_ekubo_fees_collected(raw)
    except (NormalizationError, IndexError, ValueError):
        # Tolerate decode failures during M1 — the Cairo struct deserializer
        # is incomplete; full deserialization lands in M2.
        return None
    return None


def _normalize_ekubo_swapped(raw: dict[str, Any]) -> SwapEvent:
    """Decode an Ekubo Swapped event.

    Ekubo's event schema (Cairo-1):
      Swapped(
        locker: ContractAddress,           // key
        pool_key_hash: felt252,            // key — the pool_id we use
        params: SwapParameters,            // data: amount_specified, is_token1_, …
        delta: Delta,                      // data: amount0, amount1 (signed i129)
        sqrt_ratio_after: u256,            // data
        tick_after: i129,                  // data
        liquidity_after: u128,             // data
      )

    Real implementation requires decoding signed i129 from felts; this is a
    skeleton that captures the shape and TODO-marks the signed-int decode.
    """
    keys = raw["keys"]
    data = raw["data"]
    # Ekubo's Swapped event (observed live):
    #   data[0] = locker
    #   data[1] = pool_key.token0
    #   data[2] = pool_key.token1
    #   data[3] = pool_key.fee (compact)
    #   data[4] = pool_key.tick_spacing
    #   data[5] = pool_key.extension (or zero)
    #   data[6..] = SwapParameters + Delta + sqrt_ratio_after + tick_after + liquidity_after
    # We use data[0] as the pool-identifier proxy until full PoolKey hashing
    # lands in M2's normalizer upgrade.
    if not data:
        raise NormalizationError("Swapped event missing data")
    pool_id = data[0] if data else "0x0"

    # TODO: full Cairo-struct decoding via starknet-py's CairoSerializer.
    # For now, capture the rest as opaque hex; the math kernels don't rely
    # on the per-field decode for the M1 metrics.
    swap = Swap(
        pool_id=pool_id,
        direction=SwapDirection.TOKEN0_TO_TOKEN1,  # TODO: decode from params
        amount0_in_wei=int(data[0], 16) if data else 0,
        amount1_in_wei=int(data[1], 16) if len(data) > 1 else 0,
        fee_amount_wei=0,                   # TODO
        fee_token_is_0=True,
        pool_price_pre=0.0,                 # TODO: derive from pre-state
        pool_price_post=0.0,                # TODO: derive from sqrt_ratio_after
        pool_tick_pre=0,
        pool_tick_post=0,
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
    )
    return SwapEvent(
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
        swap=swap,
    )


def _normalize_ekubo_position_updated(raw: dict[str, Any]) -> PositionUpdatedEvent | PositionOpenedEvent | PositionClosedEvent:
    """Decode an Ekubo PositionUpdated event into open/update/close.

    Distinguishes:
      - Open: liquidity_delta > 0 and the pool has no prior liquidity for the salt
      - Update: liquidity_delta != 0 and prior position exists
      - Close: net liquidity = 0 after the update

    Without prior-state context here, returns a generic PositionUpdatedEvent
    and lets the state-replay layer classify.
    """
    keys = raw["keys"]
    data = raw["data"]
    pool_id = keys[1] if len(keys) > 1 else ""
    return PositionUpdatedEvent(
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
        pool_id=pool_id,
        nft_id=int(data[0], 16) if data else 0,
        L_delta=0.0,                       # TODO: decode signed liquidity_delta
        fees_collected_token0_wei=0,
        fees_collected_token1_wei=0,
    )


def _normalize_ekubo_fees_collected(raw: dict[str, Any]) -> PositionUpdatedEvent:
    """Decode a fee-collection event."""
    keys = raw["keys"]
    data = raw["data"]
    pool_id = keys[1] if len(keys) > 1 else ""
    fees0 = int(data[0], 16) if data else 0
    fees1 = int(data[1], 16) if len(data) > 1 else 0
    return PositionUpdatedEvent(
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
        pool_id=pool_id,
        nft_id=0,
        L_delta=0.0,
        fees_collected_token0_wei=fees0,
        fees_collected_token1_wei=fees1,
    )


# ---------- Pragma oracle update normalizer ----------
def normalize_pragma_event(raw: dict[str, Any]) -> Optional[OracleUpdateEvent]:
    """Pragma's `SubmittedSpotEntry` event — map to OracleUpdateEvent."""
    keys = raw.get("keys", [])
    if not keys:
        return None
    if keys[0] != selector_hex("SubmittedSpotEntry"):
        return None
    # TODO: full decode of the Pragma struct; placeholder.
    data = raw["data"]
    return OracleUpdateEvent(
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
        oracle=int(raw.get("from_address", "0x0"), 16),
        pair_id=int(data[0], 16) if data else 0,
        price=0.0,                         # TODO: decode price field
        decimals=8,
        n_sources_aggregated=0,
    )


# ---------- DeFi Spring distributor normalizer ----------
def normalize_gauge_event(raw: dict[str, Any]) -> Optional[GaugeRateAdjustedEvent]:
    """A DeFi Spring rate-adjustment event → GaugeRateAdjustedEvent."""
    keys = raw.get("keys", [])
    if not keys:
        return None
    if keys[0] != selector_hex("RateAdjusted"):
        return None
    data = raw["data"]
    return GaugeRateAdjustedEvent(
        block_number=raw["block_number"],
        block_timestamp=raw.get("block_timestamp", 0),
        tx_hash=raw.get("transaction_hash", ""),
        log_index=0,
        distributor=int(raw.get("from_address", "0x0"), 16),
        new_rate_per_second=int(data[0], 16) / 1e18 if data else 0.0,
        old_rate_per_second=int(data[1], 16) / 1e18 if len(data) > 1 else 0.0,
        epoch=int(data[2], 16) if len(data) > 2 else 0,
    )
