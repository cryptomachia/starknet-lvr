"""
Domain type aliases and primitive helpers.

We don't use NewType because it adds runtime indirection without static
benefit on Python 3.10+. Instead, type aliases for documentation and IDE
autocomplete; runtime checks in the constructors that need them.
"""

from __future__ import annotations
from typing import NewType
from decimal import Decimal


# ---------- Numeric primitives ----------
# Felt252 = a 252-bit integer (Cairo's native field element).
# Stored as Python int; helpers convert to/from hex.
Felt252 = int

# An Ethereum-style address; on Starknet these are full 252-bit felts.
Address = int

# Block number (Starknet mainnet); plain unsigned int.
BlockNumber = int

# Unix-seconds timestamp.
Timestamp = float

# Token wei (smallest unit; depends on token decimals).
Wei = int

# Price = token1 / token0; we use float (sufficient for 6-7 sig figs in research).
# Production-grade vault contracts MUST use 18-decimal fixed-point on u256.
Price = float

# v3-style liquidity invariant L; positive float.
Liquidity = float

# v3 tick index (signed integer, unbounded in principle).
Tick = int

# Token decimal places (typically 6, 8, or 18).
Decimals = int

# Basis points: 1 bp = 0.01%.
Bps = float


# ---------- Pool identifier ----------
# Ekubo uses a 32-byte pool_key hash; we accept hex or felt.
PoolId = str  # hex-prefixed; e.g., "0xdead..." or felt int as decimal string.


# ---------- Felt encoding helpers ----------
SHORT_STRING_MAX_LEN = 31
FELT252_MAX = (1 << 251) + 17 * (1 << 192) + 1  # 2^251 + 17·2^192 + 1, the actual modulus


def short_string_to_felt(s: str) -> Felt252:
    """Encode an ASCII string of ≤31 chars as a Cairo short-string felt252."""
    if len(s) > SHORT_STRING_MAX_LEN:
        raise ValueError(
            f"short string too long: {len(s)} > {SHORT_STRING_MAX_LEN}: {s!r}"
        )
    if not s.isascii():
        raise ValueError(f"short string must be ASCII: {s!r}")
    return int.from_bytes(s.encode("ascii"), "big")


def felt_to_short_string(f: Felt252) -> str:
    """Decode a felt252 back to an ASCII short string. Strips leading zero bytes."""
    if f < 0 or f >= FELT252_MAX:
        raise ValueError(f"felt out of range: {f}")
    n_bytes = max(1, (f.bit_length() + 7) // 8)
    raw = f.to_bytes(n_bytes, "big")
    # strip non-ASCII / non-printable trailing bytes
    return raw.decode("ascii")


def hex_to_felt(s: str) -> Felt252:
    """0x-prefixed hex (with optional underscores) → Felt252 int."""
    s = s.replace("_", "")
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s)


def felt_to_hex(f: Felt252, pad_bytes: int = 0) -> str:
    """Felt252 int → 0x-prefixed hex string. pad_bytes=0 means no zero-padding."""
    if f < 0:
        raise ValueError(f"felt cannot be negative: {f}")
    body = format(f, "x")
    if pad_bytes > 0:
        body = body.rjust(pad_bytes * 2, "0")
    return "0x" + body


# ---------- Decimals normalization ----------
def from_wei(amount: Wei, decimals: Decimals) -> float:
    """Convert raw wei integer to a float in the token's natural unit."""
    if decimals < 0:
        raise ValueError(f"decimals must be non-negative: {decimals}")
    return amount / (10 ** decimals)


def to_wei(amount: float, decimals: Decimals) -> Wei:
    """Float in natural units → raw wei integer (truncates fractional wei)."""
    if decimals < 0:
        raise ValueError(f"decimals must be non-negative: {decimals}")
    return int(amount * (10 ** decimals))


def usd_value(amount_wei: Wei, decimals: Decimals, usd_price: float) -> float:
    """Common pattern: token amount × USD price."""
    return from_wei(amount_wei, decimals) * usd_price


# ---------- Bps helpers ----------
BPS_PER_UNIT = 10_000.0


def bps_to_fraction(bps: Bps) -> float:
    """5 bp → 0.0005."""
    return bps / BPS_PER_UNIT


def fraction_to_bps(f: float) -> Bps:
    """0.0005 → 5 bp."""
    return f * BPS_PER_UNIT


# ---------- Tick math (v3-style) ----------
# Tick i corresponds to price (1.0001)^i in standard v3.
# Ekubo allows finer tick spacing (sub-bp); the conversion is the same.
TICK_BASE = 1.0001


def tick_to_price(tick: Tick, base: float = TICK_BASE) -> Price:
    """tick i → price = base^i."""
    return base ** tick


def price_to_tick(price: Price, base: float = TICK_BASE) -> Tick:
    """price → floor(log_base(price))."""
    if price <= 0:
        raise ValueError(f"price must be positive: {price}")
    import math
    return int(math.floor(math.log(price) / math.log(base)))


def nearest_tick(price: Price, tick_spacing: int = 1, base: float = TICK_BASE) -> Tick:
    """Snap to the nearest valid tick given the pool's tick spacing."""
    raw = price_to_tick(price, base)
    return (raw // tick_spacing) * tick_spacing
