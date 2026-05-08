"""
Inferential statistics for the four hypotheses (H1-H4).

Implementations:
- Newey-West HAC standard errors  (H1, H2)
- Fama-MacBeth two-pass cross-section  (H2)
- 2SLS instrumental variable  (H3)
- Benjamini-Hochberg FDR correction across hypothesis families  (all)

Designed to slot into a panel where rows are (pool × day) and columns include
the wedge, instruments, controls.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Sequence, Tuple, List


# ---------- Newey-West HAC ----------
def newey_west_se(y: Sequence[float], x: Sequence[Sequence[float]],
                  lag: int | None = None) -> Tuple[np.ndarray, np.ndarray]:
    """OLS coefficients with Newey-West HAC standard errors.

    Args:
        y:   (n,) outcome.
        x:   (n, k) design matrix INCLUDING an intercept column.
        lag: bandwidth; default ⌈4·(n/100)^(2/9)⌉ (Newey-West 1994 rule).

    Returns:
        (beta, se) — (k,) coefficient and standard-error vectors.
    """
    Y = np.asarray(y, dtype=float)
    X = np.asarray(x, dtype=float)
    n, k = X.shape
    if lag is None:
        lag = max(1, int(np.ceil(4.0 * (n / 100.0) ** (2.0 / 9.0))))
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ Y
    resid = Y - X @ beta
    # Compute long-run variance with Bartlett kernel
    S = (X * resid[:, None]).T @ (X * resid[:, None])
    for ell in range(1, lag + 1):
        w = 1.0 - ell / (lag + 1)
        Gamma = np.zeros_like(S)
        for t in range(ell, n):
            u_t = (X[t] * resid[t]).reshape(-1, 1)
            u_tl = (X[t - ell] * resid[t - ell]).reshape(-1, 1)
            Gamma += u_t @ u_tl.T
        S += w * (Gamma + Gamma.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.diag(cov))
    return beta, se


# ---------- Fama-MacBeth ----------
@dataclass
class FMResult:
    coef: np.ndarray         # (k,) average cross-sectional coefficients
    se: np.ndarray           # (k,) Fama-MacBeth standard errors
    t_stat: np.ndarray
    n_periods: int


def fama_macbeth(y_panel: np.ndarray, x_panel: np.ndarray) -> FMResult:
    """Fama-MacBeth two-pass cross-sectional regression.

    Args:
        y_panel: (T, N) — T time periods, N assets/pools.
        x_panel: (T, N, K) — same panel structure with K regressors.

    Step 1: at each t, run cross-sectional OLS β_t = (X_t' X_t)^{-1} X_t' y_t.
    Step 2: report the time-average β̄ and its sampling SE.
    """
    T = y_panel.shape[0]
    K = x_panel.shape[2]
    betas = np.empty((T, K))
    for t in range(T):
        Xt, yt = x_panel[t], y_panel[t]
        mask = np.isfinite(yt) & np.all(np.isfinite(Xt), axis=1)
        Xt, yt = Xt[mask], yt[mask]
        if len(yt) <= K:
            betas[t] = np.nan
            continue
        betas[t] = np.linalg.lstsq(Xt, yt, rcond=None)[0]
    betas_clean = betas[~np.any(np.isnan(betas), axis=1)]
    coef = betas_clean.mean(axis=0)
    se = betas_clean.std(axis=0, ddof=1) / np.sqrt(len(betas_clean))
    t_stat = coef / np.where(se > 0, se, np.nan)
    return FMResult(coef=coef, se=se, t_stat=t_stat, n_periods=len(betas_clean))


# ---------- 2SLS IV ----------
def iv_2sls(y: np.ndarray, x_endog: np.ndarray, x_exog: np.ndarray,
            z: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Two-stage least squares.

    Args:
        y:        (n,) outcome.
        x_endog:  (n, k1) endogenous regressors.
        x_exog:   (n, k2) exogenous regressors (including intercept).
        z:        (n, m) instruments (m ≥ k1, in addition to x_exog).

    Returns:
        (beta_iv, se_iv) for [x_endog, x_exog] stacked.
    """
    Y, Xe, Xx, Z = (np.asarray(a, dtype=float) for a in (y, x_endog, x_exog, z))
    Z_full = np.hstack([Z, Xx])              # all instruments incl. exogenous
    X_full = np.hstack([Xe, Xx])
    P_z = Z_full @ np.linalg.pinv(Z_full.T @ Z_full) @ Z_full.T
    XtPzX_inv = np.linalg.inv(X_full.T @ P_z @ X_full)
    beta = XtPzX_inv @ X_full.T @ P_z @ Y
    resid = Y - X_full @ beta
    sigma2 = (resid @ resid) / (len(Y) - X_full.shape[1])
    cov = sigma2 * XtPzX_inv
    return beta, np.sqrt(np.diag(cov))


# ---------- Benjamini-Hochberg FDR ----------
def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> List[bool]:
    """Return the BH-rejection mask for a family of p-values at FDR level α."""
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranks = np.empty_like(order)
    ranks[order] = np.arange(1, n + 1)
    threshold = ranks * alpha / n
    rejected_sorted = p[order] <= threshold[order]
    # BH: largest k with p_(k) ≤ k·α/n; everything ≤ that rank is rejected.
    if rejected_sorted.any():
        k_star = np.where(rejected_sorted)[0].max()
    else:
        return [False] * n
    out = np.zeros(n, dtype=bool)
    out[order[:k_star + 1]] = True
    return out.tolist()
