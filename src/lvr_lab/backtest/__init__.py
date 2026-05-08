"""Backtest engine + strategies + metrics."""

from .engine import BacktestEngine, BacktestConfig, BacktestResult, MarketStep
from .strategies import (
    Strategy, StrategyState, StepDecision,
    UnhedgedLP, NaiveDeltaHedge, LQOptimalHedge, SqueethGammaHedge,
    RebalanceOnTrigger, FundingAwareHedge,
    STRATEGIES, get_strategy,
)
from .metrics import (
    full_report, PerformanceReport,
    total_return, annualized_return, annualized_vol,
    sharpe, sortino, calmar, max_drawdown, time_under_water,
    hit_rate, tail_ratio, turnover,
)

__all__ = [
    "BacktestEngine", "BacktestConfig", "BacktestResult", "MarketStep",
    "Strategy", "StrategyState", "StepDecision",
    "UnhedgedLP", "NaiveDeltaHedge", "LQOptimalHedge", "SqueethGammaHedge",
    "RebalanceOnTrigger", "FundingAwareHedge",
    "STRATEGIES", "get_strategy",
    "full_report", "PerformanceReport",
    "total_return", "annualized_return", "annualized_vol",
    "sharpe", "sortino", "calmar", "max_drawdown", "time_under_water",
    "hit_rate", "tail_ratio", "turnover",
]
