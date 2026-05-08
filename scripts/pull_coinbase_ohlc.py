#!/usr/bin/env python3
"""
Pull hourly OHLC bars from Coinbase Advanced Trade for ETH-USD, BTC-USD, STRK-USD
over a configurable window. Output: data/coinbase_ohlc_<symbol>.csv.

Coinbase candles endpoint:
  GET https://api.exchange.coinbase.com/products/{product_id}/candles
      ?granularity=3600  (1h)
      &start=ISO8601
      &end=ISO8601
Returns rows of [time, low, high, open, close, volume]. Max 300 candles/call.

Used by `run_backtest.py` to compute Yang-Zhang σ_realized — superior to the
close-to-close estimator the API-only scoping uses.
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


def fetch(url: str, retries: int = 3, delay: float = 0.6):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lvr-lab-research/0.1"})
            with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise RuntimeError(f"failed {url}: {last}")


def pull_ohlc(product: str, start: datetime, end: datetime, granularity: int = 3600):
    """Pull OHLC bars in <=300-candle chunks."""
    rows = []
    chunk = timedelta(seconds=granularity * 290)  # leave some headroom
    cur = start
    while cur < end:
        chunk_end = min(cur + chunk, end)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity={granularity}"
               f"&start={cur.isoformat().replace('+00:00','Z')}"
               f"&end={chunk_end.isoformat().replace('+00:00','Z')}")
        data = fetch(url)
        # Coinbase returns newest-first; sort ascending.
        for row in sorted(data, key=lambda r: r[0]):
            t, low, high, open_, close, vol = row
            rows.append({
                "ts": int(t),
                "datetime": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(vol),
            })
        cur = chunk_end
        time.sleep(0.4)  # respect Coinbase rate limit
    # Dedupe (chunks may overlap by 1 bar)
    seen = set()
    out = []
    for r in rows:
        if r["ts"] not in seen:
            seen.add(r["ts"])
            out.append(r)
    out.sort(key=lambda r: r["ts"])
    return out


if __name__ == "__main__":
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(sys.argv[1]) if len(sys.argv) > 1 else 30)
    products = ["ETH-USD", "BTC-USD", "STRK-USD"]
    for prod in products:
        print(f"[{prod}] pulling {start.isoformat()} to {end.isoformat()}...")
        try:
            rows = pull_ohlc(prod, start, end, granularity=3600)
        except Exception as e:
            print(f"  ! {prod}: {e}")
            continue
        path = OUT / f"coinbase_ohlc_{prod.lower().replace('-', '_')}.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "datetime", "open", "high", "low", "close", "volume"])
            w.writeheader()
            w.writerows(rows)
        print(f"  WROTE {path}  ({len(rows)} bars)")
    print("done.")
