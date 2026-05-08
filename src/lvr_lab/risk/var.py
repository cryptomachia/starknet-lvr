"""
Value-at-Risk and Conditional VaR (Expected Shortfall) estimators.

Three methods, each appropriate in different regimes:

  - historical_var:       empirical quantile of past returns. Assumption-light;
                          requires enough history (>~250 obs for 95% level).
  - gaussian_var:         μ + zσ. Cheap; bad for fat tails.
  - cornish_fisher_var:   Gaussian + skew/kurtosis adjustment. The middle path.

CVaR (Expected Shortfall): the average loss conditional on being worse than VaR.
The risk-coherent metric Basel III uses.

All return *positive* dollar-equivalent loss numbers (not negative returns).
A 95% VaR of $5,000 means: 5% of the time, a single-period loss exceeds $5,000.
"""

from __future__ import annotations
import math
from typing import Sequence

import numpy as np
from scipy.stats import norm


def historical_var(returns: Sequence[float], alpha: float = 0.05,
                   notional: float = 1.0) -> float:
    """Empirical α-quantile loss. Returns positive dollar value.

    Args:
        returns:   array of returns (e.g., daily log-returns).
        alpha:     tail probability. 0.05 = 95% VaR; 0.01 = 99% VaR.
        notional:  position size in USD; output scales linearly.
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 20:
        return float("nan")
    quantile = float(np.quantile(arr, alpha))
    return -quantile * notional   # flip sign so positive = loss


def gaussian_var(returns: Sequence[float], alpha: float = 0.05,
                 notional: float = 1.0) -> float:
    """μ + z_α · σ. Negative if μ > z·σ. Returns positive loss."""
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return float("nan")
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1))
    z = float(norm.ppf(alpha))
    return -(mu + z * sd) * notional


def cornish_fisher_var(returns: Sequence[float], alpha: float = 0.05,
                       notional: float = 1.0) -> float:
    """Gaussian VaR with skewness + kurtosis adjustment.

    z_CF = z + (z² - 1)·s/6 + (z³ - 3z)·k/24 - (2z³ - 5z)·s²/36
    where z = norm.ppf(alpha), s = skew, k = excess kurtosis.

    Reference: Cornish & Fisher (1937).
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 4:
        return float("nan")
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1))
    if sd == 0:
        return float("nan")
    s = float(((arr - mu) ** 3).mean() / sd ** 3)
    k = float(((arr - mu) ** 4).mean() / sd ** 4 - 3)
    z = float(norm.ppf(alpha))
    z_cf = (
        z
        + (z**2 - 1) * s / 6.0
        + (z**3 - 3 * z) * k / 24.0
        - (2 * z**3 - 5 * z) * s**2 / 36.0
    )
    return -(mu + z_cf * sd) * notional


def cvar_expected_shortfall(returns: Sequence[float], alpha: float = 0.05,
                            notional: float = 1.0) -> float:
    """Expected shortfall — average loss conditional on returns ≤ α-quantile.

    The risk-coherent measure Basel III uses (replaced VaR after 2008).
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 20:
        return float("nan")
    cutoff = np.quantile(arr, alpha)
    tail = arr[arr <= cutoff]
    if len(tail) == 0:
        return float("nan")
    return -float(tail.mean()) * notional


def var_decomposition(returns: Sequence[float], notional: float = 1.0,
                      alphas: Sequence[float] = (0.01, 0.05, 0.10)) -> dict:
    """Compute multiple VaR variants at multiple confidence levels.

    Returns a structured dict useful for risk-report tables.
    """
    out = {}
    for a in alphas:
        out[f"alpha_{a:.2f}"] = {
            "historical": historical_var(returns, a, notional),
            "gaussian": gaussian_var(returns, a, notional),
            "cornish_fisher": cornish_fisher_var(returns, a, notional),
            "cvar": cvar_expected_shortfall(returns, a, notional),
        }
    return out
