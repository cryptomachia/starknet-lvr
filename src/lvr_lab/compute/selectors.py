"""
Cairo entry-point selector computation.

A Cairo entry point's selector is `keccak256(name)` truncated to 250 bits
("starknet_keccak"). Used to build calldata for `starknet_call` RPC requests.

We use pycryptodome's keccak (which is the pre-NIST keccak Ethereum and
Starknet both use, NOT the standardized SHA3 in hashlib).
"""

from __future__ import annotations
from functools import lru_cache

try:
    from Crypto.Hash import keccak as _keccak
    _HAS_KECCAK = True
except ImportError:
    _HAS_KECCAK = False


_MASK = (1 << 250) - 1


@lru_cache(maxsize=None)
def starknet_keccak(name: str) -> int:
    """Cairo entry-point selector for `name`."""
    if not _HAS_KECCAK:
        raise ImportError("pycryptodome required: pip install pycryptodome")
    h = _keccak.new(digest_bits=256)
    h.update(name.encode())
    return int.from_bytes(h.digest(), "big") & _MASK


def selector_hex(name: str) -> str:
    """Selector in 0x-hex form, the format starknet_call expects."""
    return "0x" + format(starknet_keccak(name), "x")


# ---------- Pragma oracle interface ----------
# Confirmed against https://docs.pragma.build (Spot oracle, Cairo-1).
PRAGMA_SELECTORS = {
    "get_data_median": selector_hex,             # the canonical Pragma read
    "get_data": selector_hex,                    # variant accepting AggregationMode
    "get_data_with_USD_hop": selector_hex,
    "get_spot_median_no_older_than": selector_hex,
    "get_data_entry": selector_hex,
    "get_data_median_for_sources": selector_hex,
}


def pragma_get_data_median_selector() -> str:
    return selector_hex("get_data_median")


def pragma_get_data_selector() -> str:
    return selector_hex("get_data")


# ---------- Felt-encoding helpers ----------
def short_string_to_felt(s: str) -> int:
    """Encode a short ASCII string (≤31 chars) as a felt252 (big-endian bytes)."""
    if len(s) > 31:
        raise ValueError(f"short string too long ({len(s)}>31): {s!r}")
    return int.from_bytes(s.encode(), "big")


def short_string_to_felt_hex(s: str) -> str:
    return "0x" + format(short_string_to_felt(s), "x")
