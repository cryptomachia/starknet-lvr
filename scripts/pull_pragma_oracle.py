#!/usr/bin/env python3
"""
Read Pragma oracle prices directly from the Starknet chain via RPC.

Pragma's REST API is unreachable from many regions; the on-chain contract is
the canonical reference and is what every Starknet smart contract reads.

Pragma Oracle (mainnet): 0x2a85bd616f912537c50a49a4076db02c00b29b2cdc8a197ce92ed1837fa875b
Function:                get_data_median(DataType::SpotEntry(pair_id)) -> PragmaPricesResponse

Pair IDs (felt-encoded short strings):
    "ETH/USD"  → 19514442401534788
    "BTC/USD"  → 18669995996566340
    "STRK/USD" → 6004514686061859652

Output: data/pragma_prices.csv  + comparison to Coinbase
"""

import csv
import json
import math
import ssl
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(parents=True, exist_ok=True)

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass

# onfinality first — lava rate-limits Pragma reads with 403s.
RPCS = [
    "https://starknet.api.onfinality.io/public",
    "https://rpc.starknet.lava.build",
]

PRAGMA_ORACLE = "0x2a85bd616f912537c50a49a4076db02c00b29b2cdc8a197ce92ed1837fa875b"

# Compute the correct selector for `get_data_median` via starknet_keccak.
# Note: Pragma also exposes get_data and get_spot_median; we'll fall back to those.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "src"))
from lvr_lab.compute.selectors import (
    selector_hex, short_string_to_felt_hex,
)

GET_DATA_MEDIAN_SELECTOR = selector_hex("get_data_median")
GET_DATA_SELECTOR = selector_hex("get_data")

# DataType enum tag for SpotEntry: 0; AggregationMode::Median: 0
SPOT_ENTRY_TAG = "0x0"
AGG_MEDIAN_TAG = "0x0"

PAIRS = {
    "ETH/USD": short_string_to_felt_hex("ETH/USD"),
    "BTC/USD": short_string_to_felt_hex("BTC/USD"),
    "STRK/USD": short_string_to_felt_hex("STRK/USD"),
}


def rpc_call(method: str, params, timeout: float = 6.0):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                resp = json.loads(r.read())
                if "error" in resp:
                    last = resp["error"]
                    continue
                return resp["result"]
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"all RPCs failed: {last}")


def _try_call(selector: str, calldata: list):
    return rpc_call("starknet_call", {
        "request": {
            "contract_address": PRAGMA_ORACLE,
            "entry_point_selector": selector,
            "calldata": calldata,
        },
        "block_id": "latest",
    })


def read_pragma(pair_id_hex: str):
    """Call get_data_median(SpotEntry(pair_id)) on Pragma Oracle, with fallbacks.

    Returns the parsed PragmaPricesResponse.
    """
    # 1) get_data_median(DataType::SpotEntry(pair_id))
    attempts = [
        ("get_data_median", GET_DATA_MEDIAN_SELECTOR, [SPOT_ENTRY_TAG, pair_id_hex]),
        # 2) get_data(DataType, AggregationMode)
        ("get_data", GET_DATA_SELECTOR, [SPOT_ENTRY_TAG, pair_id_hex, AGG_MEDIAN_TAG]),
    ]
    last_err = None
    result = None
    chosen = None
    for name, sel, cd in attempts:
        try:
            result = _try_call(sel, cd)
            chosen = name
            break
        except Exception as e:
            last_err = (name, str(e))
            continue
    if result is None:
        raise RuntimeError(f"all Pragma fallbacks failed; last error: {last_err}")
    # PragmaPricesResponse (Cairo-1): always single-felt price, then 4-5 metadata fields.
    # Observed shape: [price, decimals, last_updated, num_sources, expiration, ...]
    price_raw = int(result[0], 16)
    decimals = int(result[1], 16) if len(result) > 1 else 8
    ts = int(result[2], 16) if len(result) > 2 else 0
    num_src = int(result[3], 16) if len(result) > 3 else 0
    expiry = int(result[4], 16) if len(result) > 4 else 0
    price = price_raw / 10 ** decimals
    return {
        "price": price,
        "decimals": decimals,
        "last_updated": ts,
        "num_sources": num_src,
        "expiration": expiry,
        "selector_used": chosen,
    }


def coinbase_spot(product: str):
    url = f"https://api.exchange.coinbase.com/products/{product}/ticker"
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        d = json.loads(r.read())
        return float(d["price"])


def main():
    rows = []
    print("Reading Pragma on-chain + Coinbase spot...", flush=True)
    for sym, pair_id in PAIRS.items():
        print(f"  [{sym}] reading pragma...", flush=True)
        try:
            pragma = read_pragma(pair_id)
        except Exception as e:
            print(f"  ! {sym} pragma read failed: {e}", flush=True)
            pragma = {"price": None}
        cb_product = sym.replace("/", "-")
        try:
            cb = coinbase_spot(cb_product)
        except Exception as e:
            print(f"  ! {sym} coinbase failed: {e}")
            cb = None
        if pragma.get("price") and cb:
            basis_bps = (pragma["price"] - cb) / cb * 10_000
        else:
            basis_bps = None
        row = {
            "pair": sym,
            "pragma_price": pragma.get("price"),
            "pragma_n_sources": pragma.get("num_sources"),
            "pragma_last_updated": pragma.get("last_updated"),
            "coinbase_spot": cb,
            "basis_bps": basis_bps,
        }
        rows.append(row)
        print(f"  {sym}  Pragma={pragma.get('price')}  Coinbase={cb}  basis={basis_bps}bps")

    out = DATA / "pragma_prices.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE {out}")


if __name__ == "__main__":
    main()
