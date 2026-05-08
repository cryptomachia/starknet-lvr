"""
Bootstrap CIs — block (autocorrelation) and cluster (cross-section).

block_bootstrap_ci   — Politis & Romano (1994); default b = ceil(n^{1/3}).
cluster_bootstrap_ci — Cameron-Gelbach-Miller; resample whole clusters with
                       replacement. For our wedge panel the natural cluster is
                       sector (BTC pools, ETH pools, STRK pools, stable pools).
"""

from __future__ import annotations
import numpy as np
from typing import Callable, Sequence, Tuple


def block_bootstrap_ci(
    series: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    n_resamples: int = 5000,
    block_length: int | None = None,
    confidence: float = 0.95,
    seed: int | None = 1,
) -> Tuple[float, float, float]:
    """Block bootstrap CI for a statistic over a 1D dependent series."""
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n < 2:
        nan = float("nan")
        return nan, nan, nan
    if block_length is None:
        block_length = max(2, int(np.ceil(n ** (1 / 3))))
    rng = np.random.default_rng(seed)
    point = float(statistic(arr))
    samples = np.empty(n_resamples, dtype=float)
    n_blocks = int(np.ceil(n / block_length))
    for i in range(n_resamples):
        starts = rng.integers(0, n - block_length + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block_length)[None, :]).ravel()[:n]
        samples[i] = statistic(arr[idx])
    alpha = 1 - confidence
    lo = float(np.quantile(samples, alpha / 2))
    hi = float(np.quantile(samples, 1 - alpha / 2))
    return point, lo, hi


def cluster_bootstrap_ci(
    values: Sequence[float],
    clusters: Sequence,
    statistic: Callable[[np.ndarray], float],
    n_resamples: int = 5000,
    confidence: float = 0.95,
    seed: int | None = 1,
) -> Tuple[float, float, float]:
    """Cluster bootstrap (Cameron-Gelbach-Miller).

    Resamples whole clusters with replacement; preserves within-cluster
    correlation. For our wedge panel: clusters = {BTC, ETH, STRK, stable}.

    Args:
        values:   (n,) observations.
        clusters: (n,) cluster IDs (any hashable).
        statistic: callable(np.ndarray) -> scalar.

    Returns:
        (point, ci_low, ci_high)
    """
    vals = np.asarray(values, dtype=float)
    cl = np.asarray(clusters)
    mask = ~np.isnan(vals)
    vals, cl = vals[mask], cl[mask]
    if len(vals) < 2:
        return float("nan"), float("nan"), float("nan")

    unique = np.unique(cl)
    if len(unique) < 2:
        # only one cluster — fall back to block bootstrap
        return block_bootstrap_ci(vals, statistic, n_resamples, None, confidence, seed)

    cluster_idx = {c: np.where(cl == c)[0] for c in unique}
    rng = np.random.default_rng(seed)
    point = float(statistic(vals))
    samples = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        chosen = rng.choice(unique, size=len(unique), replace=True)
        idx = np.concatenate([cluster_idx[c] for c in chosen])
        samples[i] = statistic(vals[idx])
    alpha = 1 - confidence
    lo = float(np.quantile(samples, alpha / 2))
    hi = float(np.quantile(samples, 1 - alpha / 2))
    return point, lo, hi


def sector_for_pool(sym0: str, sym1: str) -> str:
    """Map a pool's tokens to a cluster label for sector-clustered bootstrap."""
    stables = {"USDC", "USDT", "USDC.e", "DAI", "AUSD0", "CASH"}
    btc = {"WBTC", "tBTC", "LBTC", "xtBTC", "xWBTC", "xLBTC", "SolvBTC"}
    eth = {"ETH", "wstETH"}
    strk = {"STRK", "xSTRK"}
    s = {sym0, sym1}
    if s.issubset(stables):
        return "stable"
    if s & btc:
        return "btc"
    if s & eth:
        return "eth"
    if s & strk:
        return "strk"
    return "other"
