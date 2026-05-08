#!/usr/bin/env python3
"""
Pull Uniswap v3 USDC/WETH pool stats from DefiLlama yields API for cross-AMM
comparison. Output: data/uniswap_v3_scoping.csv with pool TVL, daily volume,
APY base, and computed annualized fee yield.

Frees us from The Graph paywall. DefiLlama's yields API is public and stable.
"""

import csv
import json
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


def fetch_pools():
    url = "https://yields.llama.fi/pools"
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
        return json.loads(r.read())


def main():
    print("Pulling DefiLlama pool yields...")
    raw = fetch_pools()
    pools = raw.get("data", raw) if isinstance(raw, dict) else raw

    # We want Ethereum Uniswap v3 pools that match our Ekubo target pools' token pairs
    # plus the canonical v3 control pools.
    targets = [
        ("USDC-WETH", "ethereum", "uniswap-v3"),
        ("USDC-USDT", "ethereum", "uniswap-v3"),
        ("USDC-WBTC", "ethereum", "uniswap-v3"),
        ("WBTC-WETH", "ethereum", "uniswap-v3"),
    ]

    rows = []
    for symbol, chain, project in targets:
        matches = [p for p in pools
                   if p.get("project") == project
                   and p.get("chain", "").lower() == chain
                   and p.get("symbol", "") == symbol]
        # take top 4 by TVL
        matches.sort(key=lambda p: -(p.get("tvlUsd") or 0))
        for m in matches[:4]:
            tvl = m.get("tvlUsd") or 0
            vol = m.get("volumeUsd1d") or 0
            apy = m.get("apyBase") or 0
            tier = m.get("poolMeta", "?")
            # annualized fee yield from DefiLlama's apyBase (already annualized %)
            rows.append({
                "amm": "uniswap-v3",
                "chain": "ethereum",
                "pair": symbol,
                "fee_tier": tier,
                "tvl_usd": tvl,
                "vol_24h_usd": vol,
                "fees_24h_usd": vol * float(tier.rstrip("%") or 0) / 100 if tier and "%" in tier else None,
                "fee_yield_ann_pct": apy,
            })

    print(f"got {len(rows)} v3 pools:")
    for r in rows:
        print(f"  {r['pair']:<12} {r['fee_tier']:<6} TVL=${r['tvl_usd']/1e6:>6.1f}M  "
              f"vol24h=${r['vol_24h_usd']/1e6:>6.1f}M  feeY={r['fee_yield_ann_pct']:>5.2f}%")

    out = DATA / "uniswap_v3_scoping.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE {out}")


if __name__ == "__main__":
    main()
