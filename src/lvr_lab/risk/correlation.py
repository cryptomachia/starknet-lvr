"""
Correlation matrix estimation with shrinkage.

Sample covariance is a terrible estimator at small n / large p (which is
exactly our cross-sectional regime: 14 pools × 30 days). Ledoit-Wolf
shrinkage solves it: shrink toward a structured target.

Also includes:
  - factor_decomposition: PCA-based principal components of returns
  - hierarchical_risk_parity: López de Prado HRP weights
"""

from __future__ import annotations
import numpy as np
from typing import Optional


def sample_covariance(returns_matrix: np.ndarray) -> np.ndarray:
    """Standard sample covariance. Bad at small n; provided for comparison."""
    return np.cov(returns_matrix, rowvar=False, ddof=1)


def ledoit_wolf_shrinkage(returns_matrix: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf (2003) shrinkage to constant-correlation target.

    Σ_shrunk = (1 − δ)·Σ_sample + δ·F

    where F is the average-correlation target (a structured matrix with all
    diagonal elements equal to the mean variance, all off-diagonals equal to
    the mean correlation × √(σ_i σ_j)).

    Optimal δ is computed in closed form to minimize Frobenius norm of
    estimation error.

    Returns: shrunk covariance matrix.
    """
    X = np.asarray(returns_matrix, dtype=float)
    if X.ndim != 2:
        raise ValueError("returns_matrix must be 2D (n_periods × n_assets)")
    n, p = X.shape
    if n < 2 or p < 2:
        raise ValueError(f"need n≥2 periods and p≥2 assets; got {n}×{p}")

    # Center
    X_c = X - X.mean(axis=0, keepdims=True)

    # Sample covariance
    S = (X_c.T @ X_c) / (n - 1)

    # Variance vector + std vector
    var = np.diag(S)
    std = np.sqrt(var)

    # Mean variance (target diagonal)
    mu_var = var.mean()

    # Correlation matrix
    Corr = S / np.outer(std, std)
    np.fill_diagonal(Corr, 1.0)

    # Mean off-diagonal correlation
    mask = ~np.eye(p, dtype=bool)
    mean_corr = float(Corr[mask].mean())

    # Target F: constant-correlation matrix
    F = np.outer(std, std) * mean_corr
    np.fill_diagonal(F, var)

    # Optimal shrinkage intensity δ (Ledoit-Wolf formula)
    # Asymptotically Optimal: δ ≈ (sum of asymptotic variances of S elements) / (sum of squared deviations from F)
    # Implemented via the closed-form in Ledoit-Wolf (2003) section 3:
    pi_mat = np.zeros_like(S)
    for t in range(n):
        x_t = X_c[t][:, None]
        pi_mat += (x_t @ x_t.T - S) ** 2
    pi_mat /= n
    pi = pi_mat.sum()

    rho = (np.diag(pi_mat).sum())  # diagonal contribution

    # gamma = ||S - F||_F^2
    gamma = ((S - F) ** 2).sum()

    if gamma == 0:
        delta = 0.0
    else:
        kappa = (pi - rho) / gamma
        delta = max(0.0, min(1.0, kappa / n))

    return (1 - delta) * S + delta * F


def correlation_from_covariance(cov: np.ndarray) -> np.ndarray:
    """Convert covariance matrix to correlation matrix."""
    std = np.sqrt(np.diag(cov))
    Corr = cov / np.outer(std, std)
    np.fill_diagonal(Corr, 1.0)
    return Corr


def factor_decomposition(returns_matrix: np.ndarray, k_factors: int = 3) -> dict:
    """PCA decomposition of return covariance.

    Returns:
        - explained_variance_ratio: array of k floats
        - loadings: (p × k) matrix; column k is the k-th principal component
        - factor_returns: (n × k) reconstructed factor return series

    Useful for: "the first PC explains 80% of cross-pool wedge variation,
    interpreted as the σ-regime factor."
    """
    X = np.asarray(returns_matrix, dtype=float)
    n, p = X.shape
    X_c = X - X.mean(axis=0, keepdims=True)
    cov = ledoit_wolf_shrinkage(X_c)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Sort descending
    idx = np.argsort(-eigvals)
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]
    total_var = eigvals.sum()
    if total_var == 0:
        return {"explained_variance_ratio": np.zeros(k_factors), "loadings": np.zeros((p, k_factors))}
    explained = eigvals[:k_factors] / total_var
    loadings = eigvecs[:, :k_factors]
    factor_returns = X_c @ loadings
    return {
        "explained_variance_ratio": explained,
        "loadings": loadings,
        "factor_returns": factor_returns,
        "eigenvalues": eigvals[:k_factors],
    }


def hierarchical_risk_parity(returns_matrix: np.ndarray) -> np.ndarray:
    """López de Prado HRP weights — robust alternative to MV optimization.

    Steps:
      1. Hierarchical clustering on correlation distance.
      2. Quasi-diagonalization.
      3. Recursive bisection to assign weights.

    Returns p weights summing to 1.
    """
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    X = np.asarray(returns_matrix, dtype=float)
    n, p = X.shape
    if p < 2:
        return np.ones(p)

    cov = ledoit_wolf_shrinkage(X)
    corr = correlation_from_covariance(cov)
    # Distance: sqrt((1-ρ)/2)
    dist = np.sqrt(np.clip((1 - corr) / 2, 0, 1))
    np.fill_diagonal(dist, 0.0)

    Z = linkage(squareform(dist, checks=False), method="single")

    # Quasi-diagonalize: order leaves of the clustering tree
    def get_quasi_diag(link):
        link = link.astype(int)
        sort_ix = [int(link[-1, 0]), int(link[-1, 1])]
        num_items = link[-1, 3]
        while max(sort_ix) >= num_items:
            new_sort = []
            for i in sort_ix:
                if i < num_items:
                    new_sort.append(i)
                else:
                    j = i - num_items
                    new_sort.extend([int(link[j, 0]), int(link[j, 1])])
            sort_ix = new_sort
        return sort_ix

    sort_ix = get_quasi_diag(Z)
    sort_ix = [i for i in sort_ix if i < p]   # safety

    # Recursive bisection
    w = np.ones(p)
    clusters = [list(range(p))]
    while clusters:
        # Bisect each cluster, allocate inversely proportional to cluster variance
        clusters = [c[: len(c) // 2] for c in clusters if len(c) > 1] + \
                   [c[len(c) // 2:] for c in clusters if len(c) > 1]
        new_clusters = []
        i = 0
        while i < len(clusters) - 1:
            c0, c1 = clusters[i], clusters[i + 1]
            v0 = np.diag(cov)[c0].sum()
            v1 = np.diag(cov)[c1].sum()
            if v0 + v1 > 0:
                alpha = 1 - v0 / (v0 + v1)
                w[c0] *= alpha
                w[c1] *= 1 - alpha
            new_clusters.extend([c0, c1])
            i += 2
        clusters = [c for c in new_clusters if len(c) > 1]
    s = w.sum()
    return w / s if s > 0 else w
