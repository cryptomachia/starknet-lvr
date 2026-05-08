"""
Performance metrics — Sharpe, Sortino, Calmar, max DD, hit rate, tail ratio.

All operate on a NAV time series. Annualized via `periods_per_year` (default
365 for daily series, 252 for trading-day series).
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class PerformanceReport:
    total_return: float
    annualized_return: float
    annualized_vol: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    time_under_water: float    # fraction of periods below previous high
    hit_rate: float
    tail_ratio: float          # ratio of |95th pct return| / |5th pct return|
    n_periods: int

    def __str__(self) -> str:
        return (
            f"  total ret  {self.total_return * 100:>+8.2f}%\n"
            f"  ann. ret   {self.annualized_return * 100:>+8.2f}%\n"
            f"  ann. vol   {self.annualized_vol * 100:>+8.2f}%\n"
            f"  Sharpe     {self.sharpe:>+8.2f}\n"
            f"  Sortino    {self.sortino:>+8.2f}\n"
            f"  Calmar     {self.calmar:>+8.2f}\n"
            f"  max DD     {self.max_drawdown * 100:>+8.2f}%\n"
            f"  time UW    {self.time_under_water * 100:>+8.2f}%\n"
            f"  hit rate   {self.hit_rate * 100:>+8.2f}%\n"
            f"  tail ratio {self.tail_ratio:>+8.2f}"
        )


def _returns(nav: np.ndarray) -> np.ndarray:
    if len(nav) < 2:
        return np.array([])
    return np.diff(nav) / nav[:-1]


def total_return(nav: Sequence[float]) -> float:
    arr = np.asarray(nav, dtype=float)
    if len(arr) < 2 or arr[0] == 0:
        return float("nan")
    return arr[-1] / arr[0] - 1.0


def annualized_return(nav: Sequence[float], periods_per_year: float = 365) -> float:
    arr = np.asarray(nav, dtype=float)
    n = len(arr)
    if n < 2 or arr[0] == 0:
        return float("nan")
    total = arr[-1] / arr[0] - 1.0
    years = (n - 1) / periods_per_year
    if years <= 0:
        return float("nan")
    return (1 + total) ** (1 / years) - 1


def annualized_vol(returns: np.ndarray, periods_per_year: float = 365) -> float:
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe(returns: np.ndarray, periods_per_year: float = 365,
           risk_free: float = 0.0) -> float:
    if len(returns) < 2:
        return float("nan")
    excess = returns - risk_free / periods_per_year
    sd = excess.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(excess.mean() / sd * math.sqrt(periods_per_year))


def sortino(returns: np.ndarray, periods_per_year: float = 365,
            target: float = 0.0) -> float:
    """Sharpe but only penalizing downside vol."""
    if len(returns) < 2:
        return float("nan")
    excess = returns - target / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf")
    downside_dev = math.sqrt((downside ** 2).mean())
    if downside_dev == 0:
        return float("inf")
    return float(excess.mean() / downside_dev * math.sqrt(periods_per_year))


def max_drawdown(nav: Sequence[float]) -> float:
    """Worst peak-to-trough drawdown as a negative fraction (e.g., -0.15 = 15%)."""
    arr = np.asarray(nav, dtype=float)
    if len(arr) < 2:
        return 0.0
    running_max = np.maximum.accumulate(arr)
    drawdowns = (arr - running_max) / running_max
    return float(drawdowns.min())


def time_under_water(nav: Sequence[float]) -> float:
    """Fraction of observations where NAV is below its prior peak."""
    arr = np.asarray(nav, dtype=float)
    if len(arr) < 2:
        return 0.0
    running_max = np.maximum.accumulate(arr)
    return float((arr < running_max).mean())


def calmar(nav: Sequence[float], periods_per_year: float = 365) -> float:
    """Annualized return divided by absolute max drawdown."""
    ann_ret = annualized_return(nav, periods_per_year)
    mdd = abs(max_drawdown(nav))
    if mdd == 0:
        return float("inf")
    return ann_ret / mdd


def hit_rate(returns: np.ndarray) -> float:
    """Fraction of positive return periods."""
    if len(returns) == 0:
        return float("nan")
    return float((returns > 0).mean())


def tail_ratio(returns: np.ndarray) -> float:
    """|95th percentile| / |5th percentile|. >1 = good upside fat tail."""
    if len(returns) < 20:
        return float("nan")
    p95 = abs(np.quantile(returns, 0.95))
    p5 = abs(np.quantile(returns, 0.05))
    if p5 == 0:
        return float("inf")
    return float(p95 / p5)


def turnover(positions_history: Sequence[float]) -> float:
    """Average per-period absolute change in position size, divided by avg NAV.

    Positions_history: per-step position sizes (e.g., short_eth amount).
    """
    arr = np.asarray(positions_history, dtype=float)
    if len(arr) < 2:
        return float("nan")
    deltas = np.abs(np.diff(arr))
    avg_size = np.abs(arr).mean()
    if avg_size == 0:
        return 0.0
    return float(deltas.mean() / avg_size)


def full_report(nav: Sequence[float], periods_per_year: float = 365) -> PerformanceReport:
    """Compute all metrics. The headline summary."""
    arr = np.asarray(nav, dtype=float)
    rets = _returns(arr)
    return PerformanceReport(
        total_return=total_return(arr),
        annualized_return=annualized_return(arr, periods_per_year),
        annualized_vol=annualized_vol(rets, periods_per_year),
        sharpe=sharpe(rets, periods_per_year),
        sortino=sortino(rets, periods_per_year),
        calmar=calmar(arr, periods_per_year),
        max_drawdown=max_drawdown(arr),
        time_under_water=time_under_water(arr),
        hit_rate=hit_rate(rets),
        tail_ratio=tail_ratio(rets),
        n_periods=len(arr),
    )
