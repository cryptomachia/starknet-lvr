#!/usr/bin/env python3
"""
σ_fee term structure — compute σ_fee at multiple integration windows
(1d, 3d, 7d, 30d) for each Ekubo target pool, plot the curve.

The slope of σ_fee across windows tells us whether the AMM under- or over-prices
short-vs-long term volatility. A flat term structure is a "well-priced" pool;
an upward-sloping curve means short-term σ is over-paid relative to long-term;
a downward-sloping curve means long-term σ is over-paid.

Sub-second / 1min / 5min term-structure points need swap-level data and are
M1 deliverables. The 1d/3d/7d/30d points use the daily Ekubo API aggregates.
"""

import csv
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import urllib.request
import ssl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIG = ROOT / "figures"
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import matplotlib.pyplot as plt
from lvr_lab.compute.sigma_fee import sigma_fee_from_tvl_proxy, SECONDS_PER_YEAR

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


SN = "0x534e5f4d41494e"
EKUBO = "https://prod-api.ekubo.org"

# Re-pull longer history for term structure
TARGET_PAIRS = [
    ("USDC/STRK", "0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb",
                  "0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"),
    ("USDC/ETH",  "0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb",
                  "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7"),
    ("USDC/WBTC", "0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb",
                  "0x3fe2b97c1fd336e750087d68b9b867997fd64a2661ff3ca5a7c771641e8e7ac"),
    ("USDC/USDT", "0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb",
                  "0x68f5c6a61780768455de69077e07e89787839bf8166decfbf92b645209c0fb8"),
]


def fetch(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read())


def addr_int(h: str) -> int:
    return int(h, 16) if h.startswith("0x") else int(h)


def pull_pair_daily(pair: str, a: str, b: str):
    """Pull all daily volume/fees rows for a pair. Returns list of {date, fees_usd}."""
    v = fetch(f"{EKUBO}/pair/{SN}/{a}/{b}/volume")
    tvl = fetch(f"{EKUBO}/pair/{SN}/{a}/{b}/tvl")
    # Need token decimals + USD prices. Re-use the existing tokens map.
    tokens = fetch(f"{EKUBO}/tokens")
    by_addr = {addr_int(t["address"]): t for t in tokens if t["chain_id"] == SN}
    # USDC.e fallback
    by_addr.setdefault(addr_int("0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"),
                       {"symbol": "USDC.e", "decimals": 6, "usd_price": 1.0})

    def usd_amt(amt_raw, tok_int):
        t = by_addr.get(tok_int)
        if not t:
            return None
        d = t.get("decimals") or 18
        u = t.get("usd_price")
        if u is None:
            return None
        return int(amt_raw) / 10 ** d * u

    daily = defaultdict(lambda: {"fees_usd_sides": [], "vol_usd_sides": []})
    for row in v.get("volumeByTokenByDate", []):
        dt = row["date"][:10]
        tok = int(row["token"])
        f_usd = usd_amt(row["fees"], tok)
        v_usd = usd_amt(row["volume"], tok)
        if f_usd is not None:
            daily[dt]["fees_usd_sides"].append(f_usd)
            daily[dt]["vol_usd_sides"].append(v_usd or 0)
    rows = []
    for dt, d in sorted(daily.items()):
        rows.append({
            "date": dt,
            "fees_usd": max(d["fees_usd_sides"]) if d["fees_usd_sides"] else 0.0,
            "vol_usd": max(d["vol_usd_sides"]) if d["vol_usd_sides"] else 0.0,
        })
    # current TVL (best we can do; in production use time-weighted from /tvl/...delta)
    tvl_now = 0.0
    for r in tvl.get("tvlByToken", []):
        tok_int = int(r["token"])
        amt_usd = usd_amt(r["balance"], tok_int)
        if amt_usd:
            tvl_now += amt_usd
    return rows, tvl_now


def sigma_fee_window(rows, end_date: str, window_days: int, tvl: float):
    """Aggregate fees over [end_date − window, end_date), compute σ_fee."""
    end = datetime.fromisoformat(end_date).date()
    start = end - timedelta(days=window_days)
    fees = sum(r["fees_usd"] for r in rows
               if start.isoformat() <= r["date"] < end.isoformat())
    if fees <= 0 or tvl <= 0:
        return None
    dt_seconds = window_days * 24 * 3600
    return sigma_fee_from_tvl_proxy(fees, tvl, dt_seconds)


def main():
    end_date = datetime.now(timezone.utc).date().isoformat()
    windows = [1, 3, 7, 14, 30]
    all_rows = []
    for pair, a, b in TARGET_PAIRS:
        try:
            rows, tvl = pull_pair_daily(pair, a, b)
        except Exception as e:
            print(f"  ! {pair} pull failed: {e}")
            continue
        if not rows:
            continue
        n_days = len(rows)
        end = max(r["date"] for r in rows)
        print(f"\n[{pair}]  TVL=${tvl:,.0f}  history days={n_days}  last={end}")
        term = {}
        for w in windows:
            sf = sigma_fee_window(rows, end, w, tvl)
            term[w] = sf
            print(f"  σ_fee({w:>2}d) = {sf*100 if sf else 0:>6.2f}%")
        all_rows.append({"pair": pair, "tvl_usd": tvl, **{f"sigma_fee_{w}d": term[w] for w in windows}})

    out = DATA / "sigma_fee_term_structure.csv"
    if all_rows:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
        print(f"\nWROTE {out}")

    # Plot — one curve per pool
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    colors = {"USDC/STRK": "#d6604d", "USDC/ETH": "#1f3b66",
              "USDC/WBTC": "#2a9d8f", "USDC/USDT": "#888"}
    for r in all_rows:
        xs = windows
        ys = [(r.get(f"sigma_fee_{w}d") or 0) * 100 for w in windows]
        ax.plot(xs, ys, "o-", linewidth=1.6, label=r["pair"], color=colors.get(r["pair"], "#444"))
    ax.set_xscale("log")
    ax.set_xlabel("Integration window (days)")
    ax.set_ylabel(r"$\sigma_{\rm fee}$  (annualized, %)")
    ax.set_title("σ_fee term structure — Ekubo top pools (TVL-proxy aggregator method)",
                 fontsize=10)
    ax.grid(alpha=0.18, which="both")
    ax.legend(loc="best", frameon=False, fontsize=8)
    out_fig = FIG / "fig9_sigma_fee_term.png"
    plt.savefig(out_fig, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"WROTE {out_fig}")


if __name__ == "__main__":
    main()
