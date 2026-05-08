#!/usr/bin/env python3
"""
Pull real 7-day volume / fees / TVL for top Starknet Ekubo pairs from prod-api.ekubo.org,
and compute annualized fee yield. Cross-reference realized volatility from Binance public klines.
Output: ekubo_scoping.csv + ekubo_data.json (raw payloads cached).
"""

import json
import ssl
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# macOS Python 3.13 bundled certs may be missing — fall back to certifi if available, else unverified.
_ssl_ctx = ssl.create_default_context()
try:
    import certifi  # type: ignore
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        _ssl_ctx.load_default_certs()
    except Exception:
        _ssl_ctx = ssl._create_unverified_context()
        print("[warn] using unverified SSL context (no CA bundle found)")

OUT = Path(__file__).parent
EKUBO = "https://prod-api.ekubo.org"
SN = "0x534e5f4d41494e"
NOW = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
WINDOW_END = NOW
WINDOW_START = NOW - timedelta(days=7)


def fetch_json(url: str, retries: int = 3, delay: float = 0.5):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research-pull/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise RuntimeError(f"failed {url}: {last}")


def addr_to_int(hex_or_int):
    if isinstance(hex_or_int, int):
        return hex_or_int
    s = str(hex_or_int)
    if s.startswith("0x"):
        return int(s, 16)
    return int(s)


def hex_norm(addr_hex: str) -> str:
    return "0x" + addr_hex.lower().removeprefix("0x").lstrip("0").rjust(1, "0")


# ----------------- 1. Token map -----------------
print("[1/4] tokens...")
tokens_raw = fetch_json(f"{EKUBO}/tokens")
sn_tokens = [t for t in tokens_raw if t["chain_id"] == SN]
addr2tok = {}  # int(address) -> token dict
for t in sn_tokens:
    a = addr_to_int(t["address"])
    addr2tok[a] = t

print(f"  Starknet tokens: {len(sn_tokens)}")


# Manual symbol overrides for tokens not in /tokens (bridged USDC v1, etc.)
MANUAL_SYMBOLS = {
    # bridged USDC.e (old) seen as 0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8
    int("0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8", 16):
        {"symbol": "USDC.e", "decimals": 6, "usd_price": 1.0},
}


def tok_sym(addr_int) -> str:
    t = addr2tok.get(addr_int) or MANUAL_SYMBOLS.get(addr_int)
    return t["symbol"] if t else f"0x{addr_int:x}"[:10] + "…"


def tok_dec(addr_int) -> int | None:
    t = addr2tok.get(addr_int) or MANUAL_SYMBOLS.get(addr_int)
    return t.get("decimals") if t else None


def tok_usd(addr_int):
    t = addr2tok.get(addr_int) or MANUAL_SYMBOLS.get(addr_int)
    if not t:
        return None
    return t.get("usd_price")


# ----------------- 2. Top pairs -----------------
print("[2/4] top pairs by 24h volume...")
pairs_raw = fetch_json(f"{EKUBO}/overview/pairs")
sn_pairs = [p for p in pairs_raw["topPairs"] if p["chain_id"] == SN]


def pair_vol_usd(p):
    v0 = int(p["volume0_24h"])
    v1 = int(p["volume1_24h"])
    a0 = addr_to_int(p["token0"])
    a1 = addr_to_int(p["token1"])
    d0, d1 = tok_dec(a0) or 18, tok_dec(a1) or 18
    u0, u1 = tok_usd(a0), tok_usd(a1)
    parts = []
    if u0 is not None:
        parts.append(v0 / 10 ** d0 * u0)
    if u1 is not None:
        parts.append(v1 / 10 ** d1 * u1)
    if not parts:
        return 0
    return max(parts)  # one side already represents the trade in USD


sn_pairs_sorted = sorted(sn_pairs, key=pair_vol_usd, reverse=True)[:14]
print(f"  top {len(sn_pairs_sorted)} Starknet pairs:")
for p in sn_pairs_sorted:
    a0, a1 = addr_to_int(p["token0"]), addr_to_int(p["token1"])
    print(f"    {tok_sym(a0):>10} / {tok_sym(a1):<10}  24h vol≈${pair_vol_usd(p):,.0f}")


# ----------------- 3. Per-pair 7-day series -----------------
print(f"[3/4] 7-day series per pair (window {WINDOW_START.date()} → {WINDOW_END.date()})...")
records = []
for p in sn_pairs_sorted:
    a0_hex = p["token0"]
    a1_hex = p["token1"]
    a0 = addr_to_int(a0_hex)
    a1 = addr_to_int(a1_hex)
    sym0, sym1 = tok_sym(a0), tok_sym(a1)
    pair_id = f"{sym0}/{sym1}"

    # Volume + fees
    try:
        v = fetch_json(f"{EKUBO}/pair/{SN}/{a0_hex}/{a1_hex}/volume")
    except Exception as e:
        print(f"  ! volume {pair_id}: {e}")
        continue

    # Aggregate fees and volume per token over the 7-day window
    fees_by_tok = defaultdict(int)
    vol_by_tok = defaultdict(int)
    daily_rows = []
    for row in v.get("volumeByTokenByDate", []):
        d = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
        if not (WINDOW_START <= d < WINDOW_END):
            continue
        tok_int = int(row["token"])
        fees_by_tok[tok_int] += int(row["fees"])
        vol_by_tok[tok_int] += int(row["volume"])
        daily_rows.append({"date": d.date().isoformat(), "tok": tok_int,
                           "vol": int(row["volume"]), "fees": int(row["fees"])})

    if not vol_by_tok:
        continue

    # USD aggregates: volume/fees - sum each token side, then take max (one side = trade size)
    def tok_usd_amt(amt_raw, tok_int):
        d = tok_dec(tok_int) or 18
        u = tok_usd(tok_int)
        if u is None:
            return None
        return amt_raw / 10 ** d * u

    vol_usd_sides = [tok_usd_amt(v_, t_) for t_, v_ in vol_by_tok.items()]
    vol_usd_sides = [x for x in vol_usd_sides if x is not None]
    fees_usd_sides = [tok_usd_amt(f_, t_) for t_, f_ in fees_by_tok.items()]
    fees_usd_sides = [x for x in fees_usd_sides if x is not None]
    if not vol_usd_sides or not fees_usd_sides:
        print(f"  ! {pair_id} no USD price for either side, skipping")
        continue

    vol_7d_usd = max(vol_usd_sides)  # one side ~= the other for matched swaps
    fees_7d_usd = max(fees_usd_sides)

    # TVL (current snapshot — we use this as the divisor; over 7 days TVL changes but current is the standard reference)
    try:
        tvl = fetch_json(f"{EKUBO}/pair/{SN}/{a0_hex}/{a1_hex}/tvl")
    except Exception as e:
        print(f"  ! tvl {pair_id}: {e}")
        continue
    tvl_usd = 0.0
    for r in tvl.get("tvlByToken", []):
        tok_int = int(r["token"])
        bal = int(r["balance"])
        d = tok_dec(tok_int) or 18
        u = tok_usd(tok_int)
        if u is not None:
            tvl_usd += bal / 10 ** d * u

    # Annualized fee yield = (avg daily fees) * 365 / TVL
    avg_daily_fees = fees_7d_usd / 7.0
    fee_yield_ann = (avg_daily_fees * 365) / tvl_usd if tvl_usd > 0 else None

    # Pool count (different fee tiers for the same pair)
    try:
        pools = fetch_json(f"{EKUBO}/pair/{SN}/{a0_hex}/{a1_hex}/pools")
        n_pools = len(pools.get("topPools", pools)) if isinstance(pools, dict) else len(pools)
    except Exception:
        n_pools = None

    records.append({
        "pair": pair_id,
        "sym0": sym0,
        "sym1": sym1,
        "addr0": a0_hex,
        "addr1": a1_hex,
        "vol_7d_usd": vol_7d_usd,
        "fees_7d_usd": fees_7d_usd,
        "tvl_usd_now": tvl_usd,
        "fee_yield_ann": fee_yield_ann,
        "n_pools": n_pools,
        "daily_rows": daily_rows,
    })
    print(f"    {pair_id:<22}  vol7d=${vol_7d_usd:>12,.0f}  fees7d=${fees_7d_usd:>9,.0f}  "
          f"TVL=${tvl_usd:>11,.0f}  feeY={fee_yield_ann*100 if fee_yield_ann else 0:>6.2f}%  "
          f"pools={n_pools}")
    time.sleep(0.15)


# ----------------- 4. Realized volatility from Coinbase (Binance is geo-blocked) -----------------
print("[4/4] Coinbase hourly candles for σ_realized...")

# Symbol mapping: Ekubo sym -> Coinbase product
CEX_MAP = {
    "ETH": "ETH-USD",
    "WBTC": "BTC-USD", "tBTC": "BTC-USD", "LBTC": "BTC-USD",
    "xtBTC": "BTC-USD", "xWBTC": "BTC-USD", "xLBTC": "BTC-USD",
    "SolvBTC": "BTC-USD",
    "STRK": "STRK-USD", "xSTRK": "STRK-USD",
    "wstETH": "ETH-USD",
    "EKUBO": None,
    "USDC": "STABLE", "USDC.e": "STABLE", "USDT": "STABLE",
    "DAI": "STABLE", "AUSD0": "STABLE", "CASH": "STABLE", "mRe7YIELD": "STABLE",
}


def vol_for_sym(sym):
    return CEX_MAP.get(sym)


def coinbase_candles(product: str, start_iso: str, end_iso: str, granularity: int = 3600):
    url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
           f"?granularity={granularity}&start={start_iso}&end={end_iso}")
    return fetch_json(url)


import math
import statistics

start_iso = WINDOW_START.isoformat().replace("+00:00", "Z")
end_iso = WINDOW_END.isoformat().replace("+00:00", "Z")

cex_cache = {}
# Coinbase max 300 candles per request → 7 days * 24 hr = 168 candles, fits
for sym in {"ETH-USD", "BTC-USD", "STRK-USD"}:
    try:
        kl = coinbase_candles(sym, start_iso, end_iso, 3600)
        # Coinbase returns [time, low, high, open, close, volume], newest first
        kl_sorted = sorted(kl, key=lambda r: r[0])
        closes = [float(k[4]) for k in kl_sorted]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if not rets:
            sigma_h = 0
        else:
            sd = statistics.stdev(rets)
            sigma_h = sd * math.sqrt(24 * 365)
        cex_cache[sym] = {"sigma_realized_ann": sigma_h, "n_obs": len(rets)}
        print(f"  {sym:<10}  σ_realized_ann={sigma_h*100:.2f}%  ({len(rets)} hourly bars)")
    except Exception as e:
        print(f"  ! {sym}: {e}")

# alias key-style for compatibility with binance_cache name in pair_sigma_realized
binance_cache = cex_cache


def pair_sigma_realized(rec):
    """Pick the dominant non-stable side's realized vol from Binance.
       For stable-stable: very low (use empirical 1.5%).
       For volatile-stable: use the volatile side.
       For volatile-volatile (e.g., wstETH/ETH): use the cross — approximated by the smaller component's residual.
    """
    s0_b = vol_for_sym(rec["sym0"])
    s1_b = vol_for_sym(rec["sym1"])
    if s0_b == "STABLE" and s1_b == "STABLE":
        return 0.015
    if s0_b == "STABLE" and s1_b in binance_cache:
        return binance_cache[s1_b]["sigma_realized_ann"]
    if s1_b == "STABLE" and s0_b in binance_cache:
        return binance_cache[s0_b]["sigma_realized_ann"]
    # Both volatile — use the cross. wstETH/ETH ≈ small residual; STRK/ETH ≈ STRK vol with ETH partially cancelled.
    # As a defensible approximation, use the larger of the two minus a partial-correlation discount.
    sigmas = []
    for s in [s0_b, s1_b]:
        if s and s != "STABLE" and s in binance_cache:
            sigmas.append(binance_cache[s]["sigma_realized_ann"])
    if not sigmas:
        return None
    if len(sigmas) == 1:
        return sigmas[0]
    # cross volatility ~ sqrt(sigma_a^2 + sigma_b^2 - 2*rho*sigma_a*sigma_b); assume rho=0.7 between major crypto
    rho = 0.7
    a, b = max(sigmas), min(sigmas)
    return math.sqrt(a*a + b*b - 2*rho*a*b)


# Annotate each record with σ_realized
for rec in records:
    rec["sigma_realized_ann"] = pair_sigma_realized(rec)
    rec["wedge"] = (rec["fee_yield_ann"] - rec["sigma_realized_ann"]) if (
        rec["fee_yield_ann"] is not None and rec["sigma_realized_ann"] is not None) else None


# ----------------- Save -----------------
out_json = OUT / "ekubo_data.json"
with open(out_json, "w") as f:
    json.dump({
        "window_start": WINDOW_START.isoformat(),
        "window_end": WINDOW_END.isoformat(),
        "binance_cache": binance_cache,
        "records": records,
        "n_pairs": len(records),
    }, f, indent=2, default=str)
print(f"\nWROTE {out_json}")

# CSV summary
csv_path = OUT / "ekubo_scoping.csv"
with open(csv_path, "w") as f:
    f.write("pair,vol_7d_usd,fees_7d_usd,tvl_usd_now,fee_yield_ann_pct,sigma_realized_ann_pct,wedge_pct,n_pools\n")
    for r in records:
        fy = (r["fee_yield_ann"] or 0) * 100
        sg = (r["sigma_realized_ann"] or 0) * 100
        wd = (r["wedge"] if r["wedge"] is not None else 0) * 100
        f.write(f"{r['pair']},{r['vol_7d_usd']:.0f},{r['fees_7d_usd']:.0f},"
                f"{r['tvl_usd_now']:.0f},{fy:.2f},{sg:.2f},{wd:.2f},{r['n_pools']}\n")
print(f"WROTE {csv_path}")

print()
print("SUMMARY")
print("=" * 100)
print(f"{'pair':<22} {'vol7d_usd':>14} {'fees7d_usd':>11} {'tvl_usd':>13} "
      f"{'feeY%':>7} {'σreal%':>7} {'wedge%':>8}")
for r in sorted(records, key=lambda x: -x["vol_7d_usd"]):
    fy = (r["fee_yield_ann"] or 0) * 100
    sg = (r["sigma_realized_ann"] or 0) * 100
    wd = (r["wedge"] if r["wedge"] is not None else 0) * 100
    print(f"{r['pair']:<22} {r['vol_7d_usd']:>14,.0f} {r['fees_7d_usd']:>11,.0f} "
          f"{r['tvl_usd_now']:>13,.0f} {fy:>7.2f} {sg:>7.2f} {wd:>+8.2f}")
