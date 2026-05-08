#!/usr/bin/env python3
"""
Backtest a hypothetical delta-neutral USDC/ETH LP vault on Ekubo,
hedged via short ETH-PERP, over the 30-day Coinbase OHLC window.

This is the publication figure of the proposal: a NAV curve showing
that the strategy would have produced X% return with Y% drawdown,
benchmarked against (a) HODL, (b) USDC-only, (c) raw LP no-hedge.

The vault has:
  - $100,000 deposit at t=0
  - Uniform-band LP centered on t=0 ETH price, ±10% width
  - Daily rebalance to delta-neutral by adjusting a short ETH-PERP
  - Fees accrue at observed Ekubo USDC/ETH 5bp pool rate (real)
  - LVR computed via the Milionis closed form on observed σ
  - Funding cost on the perp at a constant 10% APR (representative)

Output:
  data/vault_nav.csv         — daily NAV for vault, HODL, USDC, raw LP
  figures/fig7_vault_nav.png — publication NAV curve

Caveats (for the empirical paper):
  - Single-deposit, no rebalance-on-trigger logic (daily fixed cadence)
  - Uses Coinbase ETH/USD as both LP price reference AND hedge price (ignores basis)
  - Funding rate stubbed at 10% APR; live integration in M1
  - Assumes vault is the only LP in its range — overstates fees
"""

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvr_lab.compute.greeks import (
    Position, position_value, position_amounts, delta, impermanent_loss,
    liquidity_from_value,
)
from lvr_lab.compute.lvr import lvr_rate_in_range
from lvr_lab.compute.vol_estimators import realized_vol_yang_zhang

# ----------------- Inputs -----------------
DEPOSIT_USD = 100_000.0
WIDTH_PCT = 0.10                      # ±10% range
EKUBO_USDC_ETH_FEE_BPS = 5            # observed in scoping (5bp tier)
FUNDING_APR = 0.10                    # representative; fix at 10% / yr
HEDGE_RATIO = 1.0                     # full delta hedge
SHARE_OF_RANGE = 0.30                 # vault is 30% of in-range liquidity (conservative; pool TVL ≈ $1.2M)
DAILY_VOL_USD = 470_000.0             # observed Ekubo USDC/ETH 7d / 7 = $471K/day

DATA = ROOT / "data"
FIG = ROOT / "figures"
FIG.mkdir(parents=True, exist_ok=True)


# ----------------- Load price path -----------------
def load_eth_ohlc():
    path = DATA / "coinbase_ohlc_eth_usd.csv"
    if not path.exists():
        raise SystemExit(f"missing {path}; run scripts/pull_coinbase_ohlc.py 30")
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
    rows.sort(key=lambda r: r["ts"])
    return rows


def daily_closes(hourly):
    """Pick the last-of-day close for each UTC date."""
    by_day = {}
    for r in hourly:
        d = datetime.fromtimestamp(r["ts"], tz=timezone.utc).date()
        by_day[d] = r
    return sorted(by_day.items(), key=lambda kv: kv[0])


def main():
    ohlc = load_eth_ohlc()
    days = daily_closes(ohlc)
    if len(days) < 5:
        raise SystemExit("not enough OHLC; pull more")

    p0 = days[0][1]["close"]
    print(f"window: {days[0][0]} → {days[-1][0]}  ({len(days)} days)")
    print(f"ETH start price: ${p0:.2f}")
    print(f"ETH end price:   ${days[-1][1]['close']:.2f}")

    # Position: ±WIDTH_PCT band around p0
    p_a = p0 * (1.0 - WIDTH_PCT)
    p_b = p0 * (1.0 + WIDTH_PCT)
    L = liquidity_from_value(DEPOSIT_USD, p0, p_a, p_b)
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    x0, y0 = position_amounts(pos, p0)
    print(f"position: L={L:.4f}  range=[${p_a:.2f}, ${p_b:.2f}]")
    print(f"initial inventory: {x0:.4f} ETH + {y0:.2f} USDC = ${position_value(pos, p0):.2f}")

    # Rolling Yang-Zhang σ for the LVR calculation
    def yz_sigma_at(day_idx):
        """YZ over the trailing 7d (168 hourly bars) of OHLC ending at this day's close."""
        end_ts = days[day_idx][1]["ts"]
        start_ts = end_ts - 7 * 24 * 3600
        bars = [r for r in ohlc if start_ts <= r["ts"] <= end_ts]
        if len(bars) < 24:
            return 0.5  # fallback
        return realized_vol_yang_zhang(
            [b["open"] for b in bars],
            [b["high"] for b in bars],
            [b["low"] for b in bars],
            [b["close"] for b in bars],
            annualization_factor=24 * 365,
        )

    # Backtest: walk through days
    fees_cum = 0.0
    lvr_cum = 0.0
    funding_cum = 0.0
    hedge_pnl_cum = 0.0
    short_size_eth = HEDGE_RATIO * x0  # initial hedge

    # parallel benchmarks
    nav_vault, nav_hodl, nav_usdc, nav_raw_lp = [], [], [], []

    for i, (d, bar) in enumerate(days):
        p = bar["close"]
        # --- LP value ---
        v_lp = position_value(pos, p)
        # --- Fees: daily volume × fee_rate × share-of-range, only if in-range ---
        in_range = (p_a < p < p_b)
        daily_fee = (DAILY_VOL_USD * EKUBO_USDC_ETH_FEE_BPS / 1e4 * SHARE_OF_RANGE) if in_range else 0.0
        fees_cum += daily_fee
        # --- LVR (Milionis): rate × Δt where σ is rolling YZ ---
        sigma = yz_sigma_at(i)
        # rate_per_year × dt_years
        if in_range:
            rate = lvr_rate_in_range(pos, p, sigma)  # token1 / year
            daily_lvr = rate / 365.0
            lvr_cum += daily_lvr
        # --- Hedge mark-to-market ---
        if i > 0:
            prev_p = days[i - 1][1]["close"]
            hedge_pnl_cum += short_size_eth * (prev_p - p)  # short profits when p falls
            # funding paid on absolute notional, daily
            notional = abs(short_size_eth) * 0.5 * (prev_p + p)
            funding_cum += notional * FUNDING_APR / 365.0
        # rebalance hedge to current Δ
        short_size_eth = HEDGE_RATIO * delta(pos, p)

        nav_v = v_lp + fees_cum + hedge_pnl_cum - funding_cum
        # HODL: the (x0, y0) inventory at start, just held
        nav_h = y0 + x0 * p
        # USDC-only: $100k cash, no return
        nav_u = DEPOSIT_USD
        # Raw LP (no hedge): just LP value + fees
        nav_r = v_lp + fees_cum

        nav_vault.append(nav_v)
        nav_hodl.append(nav_h)
        nav_usdc.append(nav_u)
        nav_raw_lp.append(nav_r)

    # Save NAV CSV
    out_csv = DATA / "vault_nav.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "eth_close", "nav_vault", "nav_hodl", "nav_usdc", "nav_raw_lp",
                    "fees_cum", "lvr_cum", "hedge_pnl_cum", "funding_cum"])
        for i, (d, bar) in enumerate(days):
            w.writerow([d.isoformat(), bar["close"],
                        nav_vault[i], nav_hodl[i], nav_usdc[i], nav_raw_lp[i],
                        fees_cum if i == len(days) - 1 else "",
                        lvr_cum if i == len(days) - 1 else "",
                        hedge_pnl_cum if i == len(days) - 1 else "",
                        funding_cum if i == len(days) - 1 else ""])

    # Risk metrics
    arr = np.array(nav_vault)
    rets = np.diff(arr) / arr[:-1]
    sharpe = (rets.mean() / rets.std(ddof=1)) * math.sqrt(365) if rets.std() > 0 else float("nan")
    max_dd = ((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min()
    total_ret = arr[-1] / arr[0] - 1
    hodl_ret = nav_hodl[-1] / nav_hodl[0] - 1
    raw_lp_ret = nav_raw_lp[-1] / nav_raw_lp[0] - 1

    print()
    print("=== TERMINAL P&L DECOMPOSITION ===")
    print(f"Cum fees:       ${fees_cum:>10,.2f}")
    print(f"Cum LVR (YZ σ): ${lvr_cum:>10,.2f}  ← σ²·L·√p / 4 (Milionis)")
    print(f"Cum hedge PnL:  ${hedge_pnl_cum:>10,.2f}")
    print(f"Cum funding:    ${funding_cum:>10,.2f}")
    print()
    print("=== RETURNS ===")
    print(f"Vault total return:     {total_ret*100:>+7.2f}%")
    print(f"HODL total return:      {hodl_ret*100:>+7.2f}%")
    print(f"Raw LP (no hedge):      {raw_lp_ret*100:>+7.2f}%")
    print(f"USDC HODL:                 0.00%")
    print()
    print("=== RISK METRICS ===")
    print(f"Sharpe (daily, ann.):   {sharpe:>+6.2f}")
    print(f"Max drawdown:           {max_dd*100:>+6.2f}%")

    # Plot NAV curve
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    dates = [d for d, _ in days]
    ax.plot(dates, np.array(nav_vault) / 1000, "-", linewidth=1.8, label="delta-neutral vault", color="#1f3b66")
    ax.plot(dates, np.array(nav_hodl) / 1000, "-", linewidth=1.2, label="HODL benchmark", color="#888")
    ax.plot(dates, np.array(nav_raw_lp) / 1000, "--", linewidth=1.2, label="raw LP, no hedge", color="#d6604d")
    ax.plot(dates, np.array(nav_usdc) / 1000, ":", linewidth=1.0, label="USDC HODL", color="#666")
    ax.set_ylabel("NAV ($K)")
    ax.set_title(f"USDC/ETH delta-neutral vault — backtest "
                 f"({dates[0]} to {dates[-1]}, $100K notional)",
                 fontsize=10)
    ax.legend(loc="best", frameon=False, fontsize=8)
    ax.grid(alpha=0.18)
    fig.autofmt_xdate()

    out_fig = FIG / "fig7_vault_nav.png"
    plt.savefig(out_fig, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"\nWROTE {out_csv}")
    print(f"WROTE {out_fig}")


if __name__ == "__main__":
    main()
