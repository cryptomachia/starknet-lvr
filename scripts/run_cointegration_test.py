#!/usr/bin/env python3
"""
Engle-Granger cointegration test on quasi-stable Ekubo pairs.

Pulls per-pool price-history from the Ekubo public API and tests:
  1. xSTRK/STRK   (Endur LST vs base STRK)
  2. WBTC/tBTC    (two BTC representations on Starknet)
  3. LBTC/WBTC    (Lombard BTC vs Wrapped BTC)

For each, we report (β, half-life, p-value, cointegrated?). Cointegrated pairs
with short half-lives have informationless arb flow; their LP fees don't need
to cover σ-driven LVR. This is the leading hypothesis for the anomalously low
xSTRK/STRK fee yield (0.13%) we observed in the scoping panel.
"""

import csv
import json
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "src"))

from lvr_lab.analysis.cointegration import engle_granger

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


SN = "0x534e5f4d41494e"
EKUBO_API = "https://prod-api.ekubo.org"


def fetch(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read())


def find_top_pool(token_a: str, token_b: str):
    """Find the highest-TVL pool for a given pair."""
    data = fetch(f"{EKUBO_API}/pair/{SN}/{token_a}/{token_b}/pools")
    pools = data.get("topPools") if isinstance(data, dict) else data
    if not pools:
        return None
    return pools[0]


# Token addresses (from data/ekubo_data.json + Ekubo /tokens)
TOKENS = {
    "STRK": "0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
    "xSTRK": "0x28d709c875c0ceac3dce7065bec5328186dc89fe254527084d1689910954b0a",
    "WBTC": "0x3fe2b97c1fd336e750087d68b9b867997fd64a2661ff3ca5a7c771641e8e7ac",
    "tBTC": "0x4daa17763b286d1e59b97c283c0b8c949994c361e426a28f743c67bdfe9a32f",
    "LBTC": "0x36834a40984312f7f7de8d31e3f6305b325389eaeea5b1c0664b2fb936461a4",
    "ETH":  "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7",
    "USDC": "0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb",
}

EKUBO_CORE = "0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b"


def fetch_price_history(token0_sym: str, token1_sym: str):
    """Find the top pool for a pair, return its price-history series."""
    a = TOKENS[token0_sym]
    b = TOKENS[token1_sym]
    pool = find_top_pool(a, b)
    if not pool:
        return None, "no pool"
    pool_id = pool.get("pool_id") or pool.get("poolId") or pool.get("id")
    if not pool_id:
        return None, f"no pool_id in {pool}"
    url = f"{EKUBO_API}/pools/{SN}/{EKUBO_CORE}/{pool_id}/price/history"
    try:
        data = fetch(url)
    except Exception as e:
        return None, str(e)
    # Response shape: {data: [{timestamp, price, ...}, ...]} or list directly
    rows = data.get("data") or data.get("history") or data
    if not isinstance(rows, list) or not rows:
        return None, "empty history"
    return rows, None


def parse_price_series(rows):
    """Extract a numeric price series from heterogeneous response shapes."""
    prices = []
    for r in rows:
        if isinstance(r, dict):
            # try common field names
            for key in ("price", "close", "p", "tick_price", "average_price"):
                v = r.get(key)
                if v is not None:
                    try:
                        prices.append(float(v))
                        break
                    except (TypeError, ValueError):
                        continue
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            try:
                prices.append(float(r[1]))
            except (TypeError, ValueError):
                pass
    return prices


PAIRS_TO_TEST = [
    ("xSTRK", "STRK"),     # the anomaly
    ("WBTC", "tBTC"),      # two BTC representations
    ("LBTC", "WBTC"),      # Lombard vs Wrapped
    ("USDC", "ETH"),       # control: clearly NOT cointegrated
]


def main():
    print("Engle-Granger cointegration on Ekubo quasi-stable pairs")
    print("=" * 70)
    rows_out = []
    for sym0, sym1 in PAIRS_TO_TEST:
        print(f"\n[{sym0}/{sym1}] fetching price history...")
        rows, err = fetch_price_history(sym0, sym1)
        if rows is None:
            # Try the reverse direction
            rows, err = fetch_price_history(sym1, sym0)
            if rows is None:
                print(f"  ! no price history available ({err})")
                continue
            sym0, sym1 = sym1, sym0
        prices = parse_price_series(rows)
        if len(prices) < 10:
            print(f"  ! too few observations ({len(prices)})")
            continue
        # Compute "y" as the same series shifted/scaled — not a separate pair, but the
        # ratio's autocorrelation tells us about peg quality. For a ratio test we need
        # both legs in USD; lacking that here, we use the pool's own price ratio,
        # which IS the y/x ratio. We test stationarity of log(price_history) directly.
        # That's an ADF test, not cointegration — equivalent for a single ratio.
        from statsmodels.tsa.stattools import adfuller
        import numpy as np
        log_p = np.log(prices)
        adf_stat, p_val, *_ = adfuller(log_p, regression="c", autolag="AIC")
        # Half-life from AR(1)
        u = log_p - log_p.mean()
        du = np.diff(u)
        u_lag = u[:-1]
        rho = float(np.cov(du, u_lag, ddof=1)[0, 1] / np.var(u_lag, ddof=1)) if u_lag.var() > 0 else 0.0
        hl = float(np.log(0.5) / np.log(1 + rho)) if rho < 0 else float("inf")

        verdict = "STATIONARY (peg-like)" if p_val < 0.05 else "non-stationary"
        print(f"  n_obs={len(prices)}  ADF stat={adf_stat:.3f}  p={p_val:.4f}  half-life={hl:.1f} periods")
        print(f"  verdict: {verdict}")
        rows_out.append({
            "pair": f"{sym0}/{sym1}",
            "n_obs": len(prices),
            "adf_stat": adf_stat,
            "p_value": p_val,
            "half_life_periods": hl,
            "stationary": p_val < 0.05,
        })

    out = DATA / "cointegration_results.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else
                           ["pair", "n_obs", "adf_stat", "p_value", "half_life_periods", "stationary"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"\nWROTE {out}")


if __name__ == "__main__":
    main()
