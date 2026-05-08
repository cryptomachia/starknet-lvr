"""
Realized-volatility estimators for OHLC bar data.

Implementations:
- Close-to-close (the naive baseline used in the scoping figure)
- Parkinson (high-low range)
- Garman-Klass (uses O,H,L,C; assumes no overnight component)
- Rogers-Satchell (drift-independent intraday)
- Yang-Zhang (overnight + open + Rogers-Satchell; jump-robust + drift-independent)

All return σ on the same time-unit basis as the input bars (e.g., hourly bars →
hourly σ). Annualize via the `annualization_factor` argument or post-multiply.

References:
- Parkinson (1980).  M. Parkinson, "The Extreme Value Method..."
- Garman & Klass (1980). "On the Estimation of Security Price Volatilities..."
- Rogers & Satchell (1991). "Estimating Variance From High, Low, and Closing Prices."
- Yang & Zhang (2000). "Drift-Independent Volatility Estimation Based on..."

The Yang-Zhang estimator is preferred for low-bar-count windows (e.g., the 168
hourly bars in our 7-day scoping window) because it converges 7-14× faster than
close-to-close at the same sample size.
"""

from __future__ import annotations
import math
from typing import Sequence, Tuple
import numpy as np


# ---------- Close-to-close (baseline) ----------
def realized_vol_close_to_close(closes: Sequence[float], annualization_factor: float = 1.0) -> float:
    """sample stdev of log returns; multiplied by sqrt(annualization_factor)."""
    arr = np.asarray(closes, dtype=float)
    if len(arr) < 2:
        return float("nan")
    rets = np.diff(np.log(arr))
    return float(rets.std(ddof=1) * math.sqrt(annualization_factor))


# ---------- Parkinson (high-low range) ----------
def realized_vol_parkinson(highs: Sequence[float], lows: Sequence[float],
                           annualization_factor: float = 1.0) -> float:
    h, l = np.asarray(highs, dtype=float), np.asarray(lows, dtype=float)
    if len(h) == 0:
        return float("nan")
    log_hl_sq = np.log(h / l) ** 2
    var_hat = log_hl_sq.mean() / (4.0 * math.log(2))
    return float(math.sqrt(var_hat * annualization_factor))


# ---------- Garman-Klass ----------
def realized_vol_garman_klass(opens: Sequence[float], highs: Sequence[float],
                              lows: Sequence[float], closes: Sequence[float],
                              annualization_factor: float = 1.0) -> float:
    o, h, l, c = (np.asarray(x, dtype=float) for x in (opens, highs, lows, closes))
    if len(o) == 0:
        return float("nan")
    hl = np.log(h / l)
    co = np.log(c / o)
    var_hat = (0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2).mean()
    return float(math.sqrt(var_hat * annualization_factor))


# ---------- Rogers-Satchell ----------
def realized_vol_rogers_satchell(opens: Sequence[float], highs: Sequence[float],
                                 lows: Sequence[float], closes: Sequence[float],
                                 annualization_factor: float = 1.0) -> float:
    o, h, l, c = (np.asarray(x, dtype=float) for x in (opens, highs, lows, closes))
    if len(o) == 0:
        return float("nan")
    rs = np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)
    var_hat = rs.mean()
    return float(math.sqrt(max(var_hat, 0.0) * annualization_factor))


# ---------- Yang-Zhang ----------
def realized_vol_yang_zhang(opens: Sequence[float], highs: Sequence[float],
                            lows: Sequence[float], closes: Sequence[float],
                            annualization_factor: float = 1.0) -> float:
    """Yang-Zhang RV estimator: σ² = σ²_overnight + k σ²_open + (1−k) σ²_RS

    where σ²_overnight is variance of log(O_t / C_{t−1}),
          σ²_open is variance of log(C_t / O_t),
          σ²_RS is the Rogers-Satchell intraday estimator.
    k = 0.34 / (1.34 + (n+1)/(n−1)) minimizes estimator variance.
    """
    o, h, l, c = (np.asarray(x, dtype=float) for x in (opens, highs, lows, closes))
    n = len(o)
    if n < 2:
        return float("nan")

    log_co = np.log(c / o)
    log_oc_prev = np.log(o[1:] / c[:-1])

    var_open_to_close = log_co.var(ddof=1)
    var_overnight = log_oc_prev.var(ddof=1)

    rs = (np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o))
    var_rs = rs.mean()

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var_yz = var_overnight + k * var_open_to_close + (1 - k) * var_rs
    return float(math.sqrt(max(var_yz, 0.0) * annualization_factor))


# ---------- Bipower-variation jump-robust (extra credit) ----------
def realized_vol_bipower(closes: Sequence[float], annualization_factor: float = 1.0) -> float:
    """Bipower variation — robust to occasional price jumps.

    BV = (π/2) · (1/(n−1)) · Σ_{t=2}^{n} |r_t| · |r_{t−1}|

    Reference: Barndorff-Nielsen & Shephard (2004).
    """
    arr = np.asarray(closes, dtype=float)
    if len(arr) < 3:
        return float("nan")
    rets = np.abs(np.diff(np.log(arr)))
    bv = (math.pi / 2) * np.sum(rets[1:] * rets[:-1]) / (len(rets) - 1)
    return float(math.sqrt(bv * annualization_factor))


def all_estimators(opens, highs, lows, closes, annualization_factor: float = 1.0) -> dict:
    """Convenience: returns a dict of estimator-name → annualized σ."""
    return {
        "close_to_close": realized_vol_close_to_close(closes, annualization_factor),
        "parkinson": realized_vol_parkinson(highs, lows, annualization_factor),
        "garman_klass": realized_vol_garman_klass(opens, highs, lows, closes, annualization_factor),
        "rogers_satchell": realized_vol_rogers_satchell(opens, highs, lows, closes, annualization_factor),
        "yang_zhang": realized_vol_yang_zhang(opens, highs, lows, closes, annualization_factor),
        "bipower": realized_vol_bipower(closes, annualization_factor),
    }
