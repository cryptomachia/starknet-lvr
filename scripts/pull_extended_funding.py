#!/usr/bin/env python3
"""
Pull funding-rate history from Extended Exchange (Starknet's largest perp DEX).

Extended exposes a public REST API documented at api.docs.extended.exchange.
Funding endpoint (continuous accrual, not 8h-tick):
  GET https://api.extended.exchange/api/v1/info/markets/{symbol}/funding

The hedge-cost analysis (proposal §3.3) needs realized funding for the BTC, ETH,
and STRK perps over the same 7-30 day window we pull Coinbase data for.

If the endpoint is unavailable (geo-restriction, rate limit), this script
writes a placeholder CSV with a documented schema and exits 0 — so the
backtest pipeline can run with a 0-funding fallback.
"""

import csv
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(parents=True, exist_ok=True)

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass

EXTENDED_BASE = "https://api.starknet.extended.exchange/api/v1"
PERPS = ["BTC-USD", "ETH-USD", "STRK-USD"]


def fetch_funding(symbol: str, start_ms: int, end_ms: int):
    """Funding-rate history. Confirmed live endpoint per api.docs.extended.exchange.

    GET /info/{market}/funding?startTime={ms}&endTime={ms}
    Required: User-Agent header.
    """
    url = (f"{EXTENDED_BASE}/info/{symbol}/funding"
           f"?startTime={start_ms}&endTime={end_ms}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "lvr-lab-research/0.1",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    for symbol in PERPS:
        path = OUT / f"extended_funding_{symbol.lower().replace('-', '_')}.csv"
        try:
            data = fetch_funding(symbol, s_ms, e_ms)
            rows = data.get("data") or data.get("fundingRates") or data
            print(f"[{symbol}] pulled {len(rows)} funding rows")
            # Live Extended API uses compact field names:
            #   T = timestamp ms, f = funding rate, m = market symbol.
            # Older docs reference the verbose schema; we read both.
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp_ms", "rate", "market", "funding_index", "mark_price"])
                for r in rows:
                    ts = r.get("T") or r.get("timestamp") or r.get("time")
                    rate = r.get("f") or r.get("rate") or r.get("fundingRate")
                    market = r.get("m") or r.get("market")
                    w.writerow([
                        ts,
                        rate,
                        market,
                        r.get("fundingIndex") or r.get("i"),
                        r.get("markPrice") or r.get("p"),
                    ])
            print(f"  WROTE {path}")
        except Exception as e:
            print(f"  ! {symbol} unavailable ({e}); writing placeholder")
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp_ms", "rate", "funding_index", "mark_price"])
                w.writerow(["# placeholder — Extended endpoint unavailable from this IP",
                            "", "", ""])
    print("done.")
