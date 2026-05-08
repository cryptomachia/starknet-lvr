"""
Cointegration testing — Engle-Granger two-step.

Used to evaluate whether quasi-stable Ekubo pairs (xSTRK/STRK, WBTC/tBTC,
LBTC/WBTC) are genuinely pegged. If a pair is cointegrated with a short
half-life, the LP-bearing flow is largely informationless — fee yield doesn't
need to compensate for σ-driven LVR — and the standard wedge interpretation
breaks. This is what we suspect explains xSTRK/STRK's anomalous 0.13% fee yield.

Engle-Granger (1987) two-step:
    1. OLS:  log(y_t) = α + β log(x_t) + u_t
    2. ADF on residuals u_t:  Δu_t = ρ u_{t-1} + Σ φ_i Δu_{t-i} + ε_t
    3. Reject H_0: ρ = 0  ⇒  cointegrated

We use statsmodels for the ADF test.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Sequence, Tuple

try:
    from statsmodels.tsa.stattools import adfuller
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False


@dataclass
class CointegrationResult:
    cointegrated: bool
    p_value: float
    adf_stat: float
    beta: float                       # cointegrating coefficient
    alpha: float                      # intercept
    half_life_periods: float          # OU mean-reversion half-life of residuals
    n_obs: int
    notes: str = ""


def _ols_simple(y: np.ndarray, x: np.ndarray) -> Tuple[float, float]:
    """Simple OLS: y = α + β x. Returns (α, β)."""
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(beta[0]), float(beta[1])


def _ar1_half_life(residuals: np.ndarray) -> float:
    """OU half-life from Δu_t = ρ u_{t-1} + ε regression."""
    u = residuals
    du = np.diff(u)
    u_lag = u[:-1]
    if u_lag.std(ddof=1) == 0:
        return float("inf")
    rho = float(np.cov(du, u_lag, ddof=1)[0, 1] / np.var(u_lag, ddof=1))
    if rho >= 0:
        return float("inf")
    # Δu = ρ u, so u_{t} = (1+ρ) u_{t-1}; AR1 half-life:
    return float(np.log(0.5) / np.log(1 + rho))


def engle_granger(y: Sequence[float], x: Sequence[float],
                  alpha: float = 0.05) -> CointegrationResult:
    """Test cointegration of log(y_t) vs log(x_t).

    Args:
        y, x: positive price series (any length, same length).
        alpha: significance threshold for the ADF test.

    Returns:
        CointegrationResult with cointegrated=True iff ADF p < alpha.
    """
    if not _HAS_STATSMODELS:
        return CointegrationResult(
            cointegrated=False, p_value=float("nan"), adf_stat=float("nan"),
            beta=float("nan"), alpha=float("nan"), half_life_periods=float("nan"),
            n_obs=0, notes="statsmodels not installed; install with `pip install statsmodels`",
        )
    y_arr = np.log(np.asarray(y, dtype=float))
    x_arr = np.log(np.asarray(x, dtype=float))
    if len(y_arr) != len(x_arr):
        raise ValueError("y and x must have the same length")
    if len(y_arr) < 10:
        return CointegrationResult(
            cointegrated=False, p_value=float("nan"), adf_stat=float("nan"),
            beta=float("nan"), alpha=float("nan"), half_life_periods=float("nan"),
            n_obs=len(y_arr), notes=f"too few observations (n={len(y_arr)})",
        )

    a, b = _ols_simple(y_arr, x_arr)
    resid = y_arr - a - b * x_arr

    # ADF on residuals — no constant or trend (since residuals are mean-zero by construction)
    adf_result = adfuller(resid, regression="n", autolag="AIC")
    adf_stat, p_val = adf_result[0], adf_result[1]
    hl = _ar1_half_life(resid)

    return CointegrationResult(
        cointegrated=p_val < alpha,
        p_value=float(p_val),
        adf_stat=float(adf_stat),
        beta=b,
        alpha=a,
        half_life_periods=hl,
        n_obs=len(y_arr),
    )
