#!/usr/bin/env python3
"""
Vault backtest v2 — compares four hedge strategies:

    1. UNHEDGED         — passive Ekubo LP, no derivative leg
    2. NAIVE Δ-HEDGE    — short full Δ_LP every day (the v1 baseline)
    3. LQ-OPTIMAL HEDGE — Bouchard-style LQ-control hedge ratio
    4. SQUEETH-HEDGE    — Squeeth (ETH²) for γ-flattening + perp for residual Δ

For each, we compute daily NAV, terminal P&L decomposition, and risk metrics
(annualized Sharpe, max drawdown). Plot all four NAV curves on one chart.

Same scenario as v1: $100K USDC/ETH delta-neutral vault, ±10% range, 30-day
window, observed Ekubo USDC/ETH 5bp fee yield, share-of-range 30%.
"""

import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvr_lab.compute.greeks import (
    Position, position_value, position_amounts, delta, gamma,
    liquidity_from_value,
)
from lvr_lab.compute.lvr import lvr_rate_in_range
from lvr_lab.compute.vol_estimators import realized_vol_yang_zhang
from lvr_lab.compute.optimal_hedge import HedgeParams, optimal_hedge_ratio
from lvr_lab.compute.squeeth import (
    squeeth_size_to_flatten_gamma, squeeth_pnl_step,
)

DEPOSIT_USD = 100_000.0
WIDTH_PCT = 0.10
EKUBO_FEE_BPS = 5
FUNDING_APR_PERP = 0.10
SHARE_OF_RANGE = 0.30
DAILY_VOL_USD = 470_000.0

# Bouchard-style LQ params
RISK_AVERSION = 5e-5            # 1/USD
TXN_COST_COEF = 100.0           # USD
EXEC_DELAY_SECONDS = 30.0       # mean delay on Extended perp
SIGMA_BASIS_GUESS = 0.05        # 5% annualized basis vol (Extended-vs-Coinbase)

DATA = ROOT / "data"
FIG = ROOT / "figures"
FIG.mkdir(parents=True, exist_ok=True)


def load_eth_ohlc():
    rows = []
    with open(DATA / "coinbase_ohlc_eth_usd.csv") as f:
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
    by_day = {}
    for r in hourly:
        d = datetime.fromtimestamp(r["ts"], tz=timezone.utc).date()
        by_day[d] = r
    return sorted(by_day.items(), key=lambda kv: kv[0])


def yz_sigma_at(ohlc, day_ts: int) -> float:
    start_ts = day_ts - 7 * 24 * 3600
    bars = [r for r in ohlc if start_ts <= r["ts"] <= day_ts]
    if len(bars) < 24:
        return 0.5
    return realized_vol_yang_zhang(
        [b["open"] for b in bars],
        [b["high"] for b in bars],
        [b["low"] for b in bars],
        [b["close"] for b in bars],
        annualization_factor=24 * 365,
    )


def simulate(strategy: str, ohlc, days):
    p0 = days[0][1]["close"]
    p_a, p_b = p0 * (1 - WIDTH_PCT), p0 * (1 + WIDTH_PCT)
    L = liquidity_from_value(DEPOSIT_USD, p0, p_a, p_b)
    pos = Position(L=L, p_a=p_a, p_b=p_b)
    x0, y0 = position_amounts(pos, p0)

    fees = 0.0
    lvr = 0.0
    perp_pnl = 0.0
    perp_funding = 0.0
    sqth_pnl = 0.0
    sqth_funding = 0.0

    prev_p = p0
    prev_short_eth = 0.0
    prev_n_sqth = 0.0
    nav_series = []

    for i, (d, bar) in enumerate(days):
        p = bar["close"]
        sigma = yz_sigma_at(ohlc, bar["ts"])
        in_range = (p_a < p < p_b)
        # Fees
        if in_range:
            fees += DAILY_VOL_USD * EKUBO_FEE_BPS / 1e4 * SHARE_OF_RANGE
        # LVR (analytical, for accounting)
        if in_range and i > 0:
            r0 = lvr_rate_in_range(pos, prev_p, sigma)
            r1 = lvr_rate_in_range(pos, p, sigma)
            lvr += 0.5 * (r0 + r1) / 365.0

        # Hedge: pick strategy
        target_short = 0.0
        target_sqth = 0.0
        if strategy == "naive":
            target_short = 1.0 * delta(pos, p)            # full Δ
        elif strategy == "lq":
            params = HedgeParams(
                sigma=sigma,
                sigma_basis=SIGMA_BASIS_GUESS,
                delay_years=EXEC_DELAY_SECONDS / (365 * 24 * 3600),
                risk_aversion=RISK_AVERSION,
                txn_cost_coef=TXN_COST_COEF,
                horizon_years=1 / 365.0,
            )
            target_short = optimal_hedge_ratio(params) * delta(pos, p)
        elif strategy == "squeeth":
            # Long Squeeth to flatten γ, then short residual Δ.
            target_sqth = squeeth_size_to_flatten_gamma(pos, p)
            # residual delta in token0
            target_short = delta(pos, p) + 2.0 * target_sqth
        elif strategy == "unhedged":
            target_short = 0.0
            target_sqth = 0.0
        else:
            raise ValueError(strategy)

        # Mark-to-market
        if i > 0:
            # Vanilla perp short PnL
            perp_pnl += prev_short_eth * (prev_p - p)
            # Funding on perp short
            notional_perp = abs(prev_short_eth) * 0.5 * (prev_p + p)
            perp_funding += notional_perp * FUNDING_APR_PERP / 365.0
            # Squeeth PnL + funding
            funding_apr_sqth = max(0.05, 2 * sigma ** 2)  # 2σ² heuristic
            sp, sf = squeeth_pnl_step(prev_n_sqth, prev_p, p, funding_apr_sqth, 86400)
            sqth_pnl += sp
            sqth_funding += sf

        v_lp = position_value(pos, p)
        nav = v_lp + fees + perp_pnl - perp_funding + sqth_pnl - sqth_funding
        nav_series.append(nav)

        prev_p = p
        prev_short_eth = target_short
        prev_n_sqth = target_sqth

    return {
        "strategy": strategy,
        "fees": fees,
        "lvr": lvr,
        "perp_pnl": perp_pnl,
        "perp_funding": perp_funding,
        "sqth_pnl": sqth_pnl,
        "sqth_funding": sqth_funding,
        "nav_series": nav_series,
    }


def metrics(nav_series):
    arr = np.asarray(nav_series, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    sharpe = (rets.mean() / rets.std(ddof=1)) * math.sqrt(365) if rets.std() > 0 else float("nan")
    mdd = ((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min()
    total = arr[-1] / arr[0] - 1
    return {"total_ret": total, "sharpe": sharpe, "max_dd": mdd}


def main():
    ohlc = load_eth_ohlc()
    days = daily_closes(ohlc)
    print(f"window: {days[0][0]} → {days[-1][0]}  ({len(days)} days)")
    print(f"ETH:    ${days[0][1]['close']:.2f} → ${days[-1][1]['close']:.2f}")
    print()

    results = {}
    for s in ["unhedged", "naive", "lq", "squeeth"]:
        results[s] = simulate(s, ohlc, days)
        m = metrics(results[s]["nav_series"])
        results[s].update(m)
        print(f"--- {s.upper():>10} ---")
        print(f"  fees       ${results[s]['fees']:>10,.2f}")
        print(f"  lvr        ${results[s]['lvr']:>10,.2f}")
        print(f"  perp_pnl   ${results[s]['perp_pnl']:>10,.2f}")
        print(f"  perp_fund  ${results[s]['perp_funding']:>10,.2f}")
        print(f"  sqth_pnl   ${results[s]['sqth_pnl']:>10,.2f}")
        print(f"  sqth_fund  ${results[s]['sqth_funding']:>10,.2f}")
        print(f"  total ret  {m['total_ret']*100:>+8.2f}%")
        print(f"  Sharpe     {m['sharpe']:>+8.2f}")
        print(f"  max DD     {m['max_dd']*100:>+8.2f}%")
        print()

    # CSV
    out_csv = DATA / "vault_nav_v2.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [s for s in results])
        for i, (d, _) in enumerate(days):
            w.writerow([d.isoformat()] + [results[s]["nav_series"][i] for s in results])

    # Plot
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    dates = [d for d, _ in days]
    colors = {"unhedged": "#888", "naive": "#1f3b66",
              "lq": "#2a9d8f", "squeeth": "#d6604d"}
    labels = {"unhedged": "unhedged LP",
              "naive": "naive Δ-hedge (v1)",
              "lq": "LQ-optimal hedge (Bouchard-style)",
              "squeeth": "Δ + Squeeth γ-hedge"}
    for s, r in results.items():
        ax.plot(dates, np.asarray(r["nav_series"]) / 1000, label=labels[s], color=colors[s], linewidth=1.6)
    ax.set_ylabel("NAV ($K)")
    ax.set_title(f"USDC/ETH vault — 4 hedge strategies ({dates[0]} to {dates[-1]}, $100K)",
                 fontsize=10)
    ax.legend(loc="best", frameon=False, fontsize=8)
    ax.grid(alpha=0.18)
    fig.autofmt_xdate()
    out_fig = FIG / "fig8_vault_strategies.png"
    plt.savefig(out_fig, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"\nWROTE {out_csv}")
    print(f"WROTE {out_fig}")


if __name__ == "__main__":
    main()
