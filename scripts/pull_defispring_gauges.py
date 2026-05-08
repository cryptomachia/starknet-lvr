#!/usr/bin/env python3
"""
DeFi Spring gauge / reward distributor reads — H3's IV identification feed.

CLARIFIED MODEL (per Starknet Foundation announcements):
SNF transfers weekly STRK to participating-protocol multi-sigs; **each protocol
runs its own distributor contract**. There is no single "DeFi Spring distributor"
contract — addresses are per-protocol.

The H3 IV identification reads from each protocol's distributor:
  * Ekubo:   reward distribution via Re7 vault claims path
  * Vesu:    Vesu Curator distributor
  * Nostra:  Nostra Distributor
  * zkLend:  zkLend Reward distributor

For the M2 indexer, we use **SNF's quarterly rate-adjustment announcements**
(timestamps from blog/forum) as the primary H3 instrument Z₁ — identifiable by
block_timestamp range, no single contract address needed. Protocol-specific
gauge addresses become the secondary instrument Z₂ for protocol-level FE.

This script reads any configured distributor's events. Address discovery is a
manual research task documented in `docs/defispring_addresses.md` (TBD).
"""

import csv
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "src"))

from lvr_lab.compute.selectors import selector_hex

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


RPCS = [
    "https://starknet.api.onfinality.io/public",
    "https://rpc.starknet.lava.build",
]

# Configurable — set via env or edit. Empty = scaffold mode.
DISTRIBUTOR_ADDRESSES = {
    "ekubo": os.environ.get("DEFISPRING_EKUBO_DISTRIBUTOR", ""),
    "nostra": os.environ.get("DEFISPRING_NOSTRA_DISTRIBUTOR", ""),
    "vesu": os.environ.get("DEFISPRING_VESU_DISTRIBUTOR", ""),
}

# Standard Starknet event selectors for distributors:
EVENT_SELECTORS_OF_INTEREST = {
    "Reward": selector_hex("Reward"),                          # generic distribution event
    "Claimed": selector_hex("Claimed"),                        # individual claim
    "EpochSet": selector_hex("EpochSet"),                      # phase transition
    "RateAdjusted": selector_hex("RateAdjusted"),              # rate change (instrument Z₁)
    "GaugeWeightUpdated": selector_hex("GaugeWeightUpdated"),  # gauge vote (instrument Z₂)
}


def rpc_call(method, params, timeout=10.0):
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


def pull_distributor_events(addr: str, n_blocks: int = 100):
    """Pull recent events from a distributor contract."""
    bn = rpc_call("starknet_blockNumber", [])
    from_block = bn - n_blocks
    result = rpc_call("starknet_getEvents", {
        "filter": {
            "from_block": {"block_number": from_block},
            "to_block": {"block_number": bn},
            "address": addr,
            "chunk_size": 100,
        }
    })
    return result.get("events", []), bn


def main():
    print("DeFi Spring distributor read (M1 deliverable scaffold)")
    print("=" * 60)
    out_rows = []
    for protocol, addr in DISTRIBUTOR_ADDRESSES.items():
        if not addr:
            print(f"\n[{protocol}] no address configured "
                  f"(set DEFISPRING_{protocol.upper()}_DISTRIBUTOR)")
            print("           → scaffold mode; framework verified but no live read")
            out_rows.append({
                "protocol": protocol,
                "address": "",
                "status": "scaffold",
                "n_events": 0,
                "block_height": "",
            })
            continue
        try:
            events, bn = pull_distributor_events(addr)
            counts = {}
            for ev in events:
                sel = ev.get("keys", ["0x0"])[0]
                counts[sel] = counts.get(sel, 0) + 1
            print(f"\n[{protocol}]  addr={addr}  block={bn}")
            print(f"  events: {len(events)} total")
            for sel, n in counts.items():
                # match against known selectors
                name = next((k for k, v in EVENT_SELECTORS_OF_INTEREST.items() if v == sel), "?")
                print(f"    {sel[:18]}…  ({name})  count={n}")
            out_rows.append({
                "protocol": protocol,
                "address": addr,
                "status": "live",
                "n_events": len(events),
                "block_height": bn,
            })
        except Exception as e:
            print(f"\n[{protocol}]  read failed: {e}")
            out_rows.append({"protocol": protocol, "address": addr, "status": f"error: {e}",
                             "n_events": 0, "block_height": ""})

    out = DATA / "defispring_gauges.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["protocol", "address", "status", "n_events", "block_height"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nWROTE {out}")
    print("\nSelectors for known distributor events:")
    for name, sel in EVENT_SELECTORS_OF_INTEREST.items():
        print(f"  {name:<20} {sel}")


if __name__ == "__main__":
    main()
