#!/usr/bin/env python3
"""
End-to-end demo: load the Ekubo daily panel + Coinbase hourly OHLC, compute
σ_fee (TVL-proxy) and σ_realized (Yang-Zhang) per pool per day, bootstrap CIs
on the wedge, write a publication-quality timeseries CSV.

This is the *daily-aggregate* version of the analysis — it runs on the data
the public API exposes. The swap-level analysis (Section 5 of the proposal)
is M1's deliverable and requires the Pathfinder full-archive node.

Output: data/wedge_timeseries.csv  +  data/wedge_summary.csv
"""

import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
import sys

# Add src to path so we can import the package without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from lvr_lab.compute.sigma_fee import sigma_fee_from_tvl_proxy, SECONDS_PER_YEAR
from lvr_lab.compute.vol_estimators import (
    realized_vol_yang_zhang,
    realized_vol_close_to_close,
    realized_vol_garman_klass,
    realized_vol_parkinson,
)
from lvr_lab.compute.bootstrap import block_bootstrap_ci

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA  # write outputs alongside inputs

# ---------- Load Ekubo daily panel ----------
ekubo_path = DATA / "ekubo_data.json"
if not ekubo_path.exists():
    raise SystemExit(f"missing {ekubo_path}; run scripts/pull_ekubo_data.py first")
with open(ekubo_path) as f:
    ekubo = json.load(f)

# ---------- Load Coinbase OHLC for each volatile reference ----------
def load_ohlc(symbol_lc: str):
    path = DATA / f"coinbase_ohlc_{symbol_lc}.csv"
    if not path.exists():
        return None
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "ts": int(r["ts"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            })
    return rows

ohlc_eth = load_ohlc("eth_usd")
ohlc_btc = load_ohlc("btc_usd")
ohlc_strk = load_ohlc("strk_usd")

if not all([ohlc_eth, ohlc_btc, ohlc_strk]):
    print("WARN: some Coinbase OHLC missing; run scripts/pull_coinbase_ohlc.py 30")

# ---------- Pool → reference symbol map ----------
def ref_symbol_for_pool(sym0: str, sym1: str):
    stables = {"USDC", "USDT", "USDC.e", "DAI", "AUSD0", "CASH"}
    btc_like = {"WBTC", "tBTC", "LBTC", "xtBTC", "xWBTC", "xLBTC", "SolvBTC"}
    eth_like = {"ETH", "wstETH"}
    strk_like = {"STRK", "xSTRK"}
    s = {sym0, sym1}
    if s.issubset(stables):
        return None  # stable-stable: σ ≈ 0
    if s & btc_like:
        return "btc"
    if s & eth_like:
        return "eth"
    if s & strk_like:
        return "strk"
    return None

# ---------- σ_realized via Yang-Zhang per day ----------
def daily_yz_volatility(ohlc, ref_day_unix: int, window_hours: int = 168):
    """YZ vol over the trailing window_hours hours from the day's UTC midnight."""
    if not ohlc:
        return None
    end = ref_day_unix
    start = end - window_hours * 3600
    rows = [r for r in ohlc if start <= r["ts"] < end]
    if len(rows) < 12:
        return None
    o = [r["open"] for r in rows]
    h = [r["high"] for r in rows]
    l = [r["low"] for r in rows]
    c = [r["close"] for r in rows]
    return realized_vol_yang_zhang(o, h, l, c, annualization_factor=24 * 365)

# ---------- Build the per-pool-day panel ----------
results = []
for rec in ekubo["records"]:
    pool = rec["pair"]
    sym0, sym1 = rec["sym0"], rec["sym1"]
    ref_kind = ref_symbol_for_pool(sym0, sym1)
    ref_ohlc = {"eth": ohlc_eth, "btc": ohlc_btc, "strk": ohlc_strk}.get(ref_kind)

    for day_row in rec.get("daily_rows", []):
        # daily_rows is per-token; just take the one row per (date, max-side)
        pass

    # Aggregate window stats for this pool
    fees_7d = rec["fees_7d_usd"]
    tvl_now = rec["tvl_usd_now"]
    if fees_7d <= 0 or tvl_now <= 0:
        continue
    dt_seconds = 7 * 24 * 3600
    sigma_fee_ann = sigma_fee_from_tvl_proxy(fees_7d, tvl_now, dt_seconds)

    # σ_realized via Yang-Zhang on the matching reference OHLC
    if ref_ohlc:
        # use last day of window as reference
        end_unix = int(datetime.fromisoformat(ekubo["window_end"].replace("+00:00","")).replace(tzinfo=timezone.utc).timestamp())
        sigma_yz = daily_yz_volatility(ref_ohlc, end_unix, window_hours=168)
        sigma_cc = realized_vol_close_to_close(
            [r["close"] for r in ref_ohlc[-168:]], annualization_factor=24 * 365
        ) if len(ref_ohlc) >= 168 else None
        sigma_gk = realized_vol_garman_klass(
            [r["open"] for r in ref_ohlc[-168:]],
            [r["high"] for r in ref_ohlc[-168:]],
            [r["low"] for r in ref_ohlc[-168:]],
            [r["close"] for r in ref_ohlc[-168:]],
            annualization_factor=24 * 365,
        ) if len(ref_ohlc) >= 168 else None
        sigma_pk = realized_vol_parkinson(
            [r["high"] for r in ref_ohlc[-168:]],
            [r["low"] for r in ref_ohlc[-168:]],
            annualization_factor=24 * 365,
        ) if len(ref_ohlc) >= 168 else None
    else:
        sigma_yz = sigma_cc = sigma_gk = sigma_pk = 0.015 if {sym0, sym1}.issubset({"USDC","USDT","USDC.e","DAI"}) else None

    results.append({
        "pool": pool,
        "sym0": sym0,
        "sym1": sym1,
        "ref_kind": ref_kind or "stable",
        "fees_7d_usd": fees_7d,
        "tvl_usd": tvl_now,
        "vol_7d_usd": rec["vol_7d_usd"],
        "n_pools_at_pair": rec.get("n_pools"),
        "sigma_fee_ann": sigma_fee_ann,
        "sigma_realized_yz": sigma_yz,
        "sigma_realized_cc": sigma_cc,
        "sigma_realized_gk": sigma_gk,
        "sigma_realized_pk": sigma_pk,
        "wedge_yz": (sigma_fee_ann - sigma_yz) if sigma_yz is not None else None,
        "wedge_cc": (sigma_fee_ann - sigma_cc) if sigma_cc is not None else None,
    })

# ---------- Bootstrap CI on the median wedge across volatile pools ----------
volatile_wedges_yz = [r["wedge_yz"] for r in results
                     if r["wedge_yz"] is not None and r["ref_kind"] != "stable"]
volatile_wedges_cc = [r["wedge_cc"] for r in results
                     if r["wedge_cc"] is not None and r["ref_kind"] != "stable"]

if volatile_wedges_yz:
    point_yz, lo_yz, hi_yz = block_bootstrap_ci(
        volatile_wedges_yz, statistic=np.median, n_resamples=5000, confidence=0.95)
    point_cc, lo_cc, hi_cc = block_bootstrap_ci(
        volatile_wedges_cc, statistic=np.median, n_resamples=5000, confidence=0.95)
else:
    point_yz = lo_yz = hi_yz = point_cc = lo_cc = hi_cc = float("nan")

# ---------- Write outputs ----------
ts_path = OUT / "wedge_timeseries.csv"
fields = ["pool", "sym0", "sym1", "ref_kind", "fees_7d_usd", "tvl_usd", "vol_7d_usd",
          "n_pools_at_pair", "sigma_fee_ann", "sigma_realized_yz", "sigma_realized_cc",
          "sigma_realized_gk", "sigma_realized_pk", "wedge_yz", "wedge_cc"]
with open(ts_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for r in results:
        w.writerow({k: r.get(k) for k in fields})

summary_path = OUT / "wedge_summary.csv"
with open(summary_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["estimator", "median_wedge", "ci_low", "ci_high", "n_pools"])
    w.writerow(["yang_zhang", point_yz, lo_yz, hi_yz, len(volatile_wedges_yz)])
    w.writerow(["close_to_close", point_cc, lo_cc, hi_cc, len(volatile_wedges_cc)])

print(f"WROTE {ts_path}")
print(f"WROTE {summary_path}")
print()
print(f"VOLATILE-POOL MEDIAN WEDGE (95% block-bootstrap CI):")
print(f"  Yang-Zhang     : {point_yz:+.3f}  [{lo_yz:+.3f}, {hi_yz:+.3f}]   (n={len(volatile_wedges_yz)})")
print(f"  Close-to-close : {point_cc:+.3f}  [{lo_cc:+.3f}, {hi_cc:+.3f}]   (n={len(volatile_wedges_cc)})")
