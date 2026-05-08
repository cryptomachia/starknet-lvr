#!/usr/bin/env python3
"""
Run H1-H4 on the real wedge timeseries. Produces:
  data/h1_h4_results.csv
  data/h1_h4_summary.txt

H1: Are incentive-free pools fairly priced? Test on USDC/USDT 1bp.
H2: What explains pool-by-pool wedge variation? Cross-section regression.
H3: Are DeFi Spring incentives doing their job? IV-2SLS using a proxy
    instrument (DefiLlama tracks pool age and DeFi Spring participation).
    Stub for now — flagged.
H4: Does pool quality predict token value? Cross-section across STRK,
    EKUBO, xSTRK using market-cap-to-fee-revenue ratios.

Note: These are *preliminary* tests on the 14-pool, 30-day window data we have.
The empirical paper will replicate with the swap-level panel (~5,000 obs/pool).
"""

import csv
import math
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "src"))

from lvr_lab.analysis.hypothesis_tests import (
    newey_west_se, fama_macbeth, benjamini_hochberg,
)
from lvr_lab.compute.bootstrap import (
    block_bootstrap_ci, cluster_bootstrap_ci, sector_for_pool,
)


def load_panel():
    rows = []
    with open(DATA / "wedge_timeseries.csv") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def parse(x, default=None):
    try:
        return float(x) if x not in ("", None) else default
    except ValueError:
        return default


def main():
    rows = load_panel()
    print(f"loaded {len(rows)} pool rows")

    # ------------- H1: stable-stable wedge ≠ 0? -------------
    stables = [r for r in rows
               if r["sym0"] in ("USDC", "USDT", "USDC.e", "DAI")
               and r["sym1"] in ("USDC", "USDT", "USDC.e", "DAI")]
    print()
    print("=" * 70)
    print(f"H1: Stable-stable wedge (n={len(stables)} pools)")
    print("=" * 70)
    for r in stables:
        print(f"  {r['pool']:<14}  wedge_yz = {parse(r['wedge_yz']):+.4f}  "
              f"σ_fee = {parse(r['sigma_fee_ann']):.4f}  σ_real = {parse(r['sigma_realized_yz']):.4f}")
    wedges_stable = [parse(r["wedge_yz"]) for r in stables if parse(r["wedge_yz"]) is not None]
    if len(wedges_stable) >= 2:
        x = np.array([1.0] * len(wedges_stable)).reshape(-1, 1)
        y = np.array(wedges_stable)
        beta, se = newey_west_se(y, x, lag=1)
        t_stat = beta[0] / se[0] if se[0] > 0 else float("nan")
        # Approximate p (two-sided) using normal approximation (low df, but warning is fine for n=3)
        from math import erf, sqrt
        p_two = 2 * (1 - 0.5 * (1 + erf(abs(t_stat) / sqrt(2))))
        print()
        print(f"  H1 mean wedge: {beta[0]:+.4f}  NW-SE: {se[0]:.4f}  t-stat: {t_stat:+.2f}  p≈{p_two:.4f}")
        print(f"  Reject H0:wedge=0 at 5%? {'YES' if p_two < 0.05 else 'no (underpowered, n=3)'}")
    h1_result = {"n": len(stables), "mean_wedge": beta[0] if len(wedges_stable) >= 2 else None}

    # ------------- H2: cross-section — what drives the wedge? -------------
    print()
    print("=" * 70)
    print(f"H2: Cross-section regression of wedge on (log_TVL, log_volume, σ_realized)")
    print("=" * 70)
    # Build design matrix
    use = []
    for r in rows:
        sigma = parse(r["sigma_realized_yz"])
        wedge = parse(r["wedge_yz"])
        tvl = parse(r["tvl_usd"])
        vol = parse(r["vol_7d_usd"])
        if all(x is not None and x > 0 for x in (sigma, tvl, vol)) and wedge is not None:
            use.append({"pool": r["pool"], "wedge": wedge, "sigma": sigma,
                        "log_tvl": math.log(tvl), "log_vol": math.log(vol)})
    print(f"  {len(use)} pools usable")
    Y = np.array([u["wedge"] for u in use])
    X = np.column_stack([np.ones(len(use)),
                         [u["log_tvl"] for u in use],
                         [u["log_vol"] for u in use],
                         [u["sigma"] for u in use]])
    beta_h2, se_h2 = newey_west_se(Y, X, lag=1)
    t_h2 = beta_h2 / np.where(se_h2 > 0, se_h2, np.nan)
    coef_names = ["intercept", "log_tvl", "log_vol", "sigma"]
    print(f"  {'coef':<12}{'estimate':>12}{'NW-SE':>12}{'t-stat':>10}")
    for n, b, s, t in zip(coef_names, beta_h2, se_h2, t_h2):
        print(f"  {n:<12}{b:>+12.4f}{s:>12.4f}{t:>+10.2f}")
    h2_result = dict(zip(coef_names, beta_h2))

    # ------------- H3: DeFi Spring identification (stub) -------------
    print()
    print("=" * 70)
    print("H3: DeFi Spring incentive effect on the wedge — IV-2SLS (skeleton)")
    print("=" * 70)
    print("  STATUS: instrumented stubs. Real instrument (DeFi Spring rate adjustments")
    print("          + gauge votes) requires on-chain reads from Starknet Foundation")
    print("          gauge contracts. Listed as M1 deliverable.")
    print("          Code path tested in tests/test_hypothesis_tests.py::test_iv_2sls_identification.")

    # ------------- H4: pool quality vs token valuation (preliminary) -------------
    print()
    print("=" * 70)
    print("H4: Does pool quality (sigma_fee) predict token Ekubo penetration?")
    print("=" * 70)
    # Aggregate per token: total fees and TVL across pools where the token appears
    tokens_of_interest = ["STRK", "ETH", "WBTC", "EKUBO", "USDC", "xSTRK"]
    by_token = {}
    for r in rows:
        for tok in (r["sym0"], r["sym1"]):
            if tok not in tokens_of_interest:
                continue
            agg = by_token.setdefault(tok, {"fees_7d": 0.0, "tvl": 0.0, "vol_7d": 0.0, "n_pools": 0})
            agg["fees_7d"] += parse(r["fees_7d_usd"]) or 0.0
            agg["tvl"] += parse(r["tvl_usd"]) or 0.0
            agg["vol_7d"] += parse(r["vol_7d_usd"]) or 0.0
            agg["n_pools"] += 1
    print(f"  {'token':<8}{'pools':>7}{'TVL_usd':>14}{'vol_7d':>14}{'fees_7d':>12}{'fee_yield_ann':>15}")
    for tok, agg in sorted(by_token.items(), key=lambda kv: -kv[1]["tvl"]):
        fee_yield = agg["fees_7d"] / agg["tvl"] * 365 / 7 if agg["tvl"] > 0 else 0
        print(f"  {tok:<8}{agg['n_pools']:>7}{agg['tvl']:>14,.0f}{agg['vol_7d']:>14,.0f}"
              f"{agg['fees_7d']:>12,.2f}{fee_yield*100:>14.2f}%")

    # ------------- Sector-clustered bootstrap on the panel-wide median wedge -------------
    print()
    print("=" * 70)
    print("Sector-clustered bootstrap on median wedge across the 14-pool panel")
    print("=" * 70)
    panel_wedges = []
    panel_clusters = []
    for r in rows:
        wedge_yz = parse(r["wedge_yz"])
        if wedge_yz is None:
            continue
        panel_wedges.append(wedge_yz)
        panel_clusters.append(sector_for_pool(r["sym0"], r["sym1"]))
    point, lo, hi = cluster_bootstrap_ci(
        panel_wedges, panel_clusters, statistic=np.median,
        n_resamples=5000, confidence=0.95, seed=1,
    )
    print(f"  panel median wedge (sector-clustered 95% CI):  "
          f"{point:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
    print(f"  n_pools={len(panel_wedges)}  sectors={sorted(set(panel_clusters))}")
    print(f"  Note: the cluster bootstrap is wider than block bootstrap because")
    print(f"        within-sector pools (e.g., all BTC pools) are correlated.")

    # ------------- BH-FDR -------------
    print()
    print("=" * 70)
    print("Multiple-test correction (Benjamini-Hochberg, FDR=5%)")
    print("=" * 70)
    # Use H1 p, H2 sigma-coef p, etc. — for demonstration only with such small n
    p_h1 = p_two if 'p_two' in dir() else None
    # H2 σ-coefficient p-value approximation
    if len(use) > 4:
        from math import erf, sqrt
        p_h2_sigma = 2 * (1 - 0.5 * (1 + erf(abs(t_h2[3]) / sqrt(2))))
    else:
        p_h2_sigma = None
    pvals = [p for p in (p_h1, p_h2_sigma) if p is not None]
    if pvals:
        rej = benjamini_hochberg(pvals, alpha=0.05)
        print(f"  p-values: {pvals}")
        print(f"  rejected at 5% FDR: {rej}")

    # ------------- Save -------------
    out_csv = DATA / "h1_h4_results.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["test", "metric", "value"])
        w.writerow(["H1", "n_pools", h1_result["n"]])
        if h1_result["mean_wedge"] is not None:
            w.writerow(["H1", "mean_wedge", h1_result["mean_wedge"]])
        for n, b, s, t in zip(coef_names, beta_h2, se_h2, t_h2):
            w.writerow([f"H2:{n}", "coef", b])
            w.writerow([f"H2:{n}", "se", s])
            w.writerow([f"H2:{n}", "t_stat", t])
    print(f"\nWROTE {out_csv}")


if __name__ == "__main__":
    main()
