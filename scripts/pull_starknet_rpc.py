#!/usr/bin/env python3
"""
Pull recent Ekubo Core (singleton) events from a public Starknet RPC.

Validates the M1 architecture: shows we can read swap events from the chain,
not just rely on the daily-aggregate API. Saves a sample of decoded events
to data/starknet_events_sample.json.

Public RPCs tried in order: lava → onfinality. Both are rate-limited but free.

Ekubo Core on Starknet mainnet:
    0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b
    (https://docs.ekubo.org/integration-guides/reference/starknet-contracts)
"""

import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(parents=True, exist_ok=True)

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


RPCS = [
    "https://rpc.starknet.lava.build",
    "https://starknet.api.onfinality.io/public",
]
EKUBO_CORE = "0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b"


def rpc_call(method: str, params, timeout: float = 15.0):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "lvr-lab/0.1"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                resp = json.loads(r.read())
                if "error" in resp:
                    last = resp["error"]
                    continue
                return resp["result"]
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"all RPCs failed for {method}: {last}")


def main():
    print("[1/3] block number...")
    bn = rpc_call("starknet_blockNumber", [])
    print(f"  current block: {bn}")

    n_blocks = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    from_block = bn - n_blocks
    print(f"[2/3] pulling Ekubo Core events for blocks [{from_block}, {bn}]...")

    all_events = []
    cont_token = None
    chunks = 0
    while True:
        params = [{
            "filter": {
                "from_block": {"block_number": from_block},
                "to_block": {"block_number": bn},
                "address": EKUBO_CORE,
                "chunk_size": 100,
                **({"continuation_token": cont_token} if cont_token else {}),
            }
        }]
        # Note: starknet_getEvents takes a single object, not array of params
        result = rpc_call("starknet_getEvents", {
            "filter": {
                "from_block": {"block_number": from_block},
                "to_block": {"block_number": bn},
                "address": EKUBO_CORE,
                "chunk_size": 100,
                **({"continuation_token": cont_token} if cont_token else {}),
            }
        })
        all_events.extend(result.get("events", []))
        cont_token = result.get("continuation_token")
        chunks += 1
        if not cont_token or chunks >= 10:
            break
        time.sleep(0.3)
    print(f"  pulled {len(all_events)} raw events across {chunks} chunks")

    # Bucket by first key (event selector)
    by_selector = {}
    for ev in all_events:
        sel = ev.get("keys", ["0x0"])[0]
        by_selector.setdefault(sel, []).append(ev)
    print("[3/3] events by selector:")
    for sel, evs in sorted(by_selector.items(), key=lambda kv: -len(kv[1])):
        print(f"  selector {sel[:18]}…  count={len(evs):>4}")

    # Save sample for reproducibility
    sample = {
        "current_block": bn,
        "from_block": from_block,
        "to_block": bn,
        "n_blocks": n_blocks,
        "n_events": len(all_events),
        "by_selector": {k: len(v) for k, v in by_selector.items()},
        "first_5_events": all_events[:5],
        "last_5_events": all_events[-5:],
    }
    out = OUT / "starknet_events_sample.json"
    with open(out, "w") as f:
        json.dump(sample, f, indent=2)
    print(f"WROTE {out}")
    return sample


if __name__ == "__main__":
    main()
