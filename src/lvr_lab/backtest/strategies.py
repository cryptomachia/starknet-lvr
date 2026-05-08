"""
Strategy abstraction for the backtest engine.

Each Strategy decides:
  1. open: at t=0, what Position(s) to construct from initial capital
  2. on_step: each tick, what hedge size to maintain (and optionally rebalance the LP)

Six concrete strategies ship out of the box:
  - UnhedgedLP                  : passive LP, no derivative leg
  - NaiveDeltaHedge             : short full Δ_LP every step
  - LQOptimalHedge              : Bouchard-style LQ hedge ratio
  - SqueethGammaHedge           : Squeeth γ-flat + residual Δ
  - RebalanceOnTrigger          : rebalance LP band when |delta_drift| > threshold
  - FundingAwareHedge           : LQ scaled by realized funding
"""

from __future__ import annotations
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..domain.position import Position
from ..domain.profile import LpProfile, UniformBandProfile
from ..compute.greeks import delta, gamma, position_value, liquidity_from_value
from ..compute.optimal_hedge import HedgeParams, optimal_hedge_ratio, funding_aware_hedge_ratio
from ..compute.squeeth import squeeth_size_to_flatten_gamma


@dataclass
class StrategyState:
    """Mutable state a strategy carries across steps."""
    position: Optional[Position] = None
    short_eth: float = 0.0           # current short size on the perp (token0 units)
    n_squeeth: float = 0.0           # current Squeeth contracts long
    rebalance_count: int = 0


@dataclass
class StepDecision:
    """Strategy's output per step: target hedge sizes."""
    target_short_token0: float = 0.0
    target_squeeth: float = 0.0
    rebalance_to: Optional[Position] = None    # if set, replace current LP


class Strategy(ABC):
    """Abstract base for backtest strategies."""

    name: str = "abstract"

    @abstractmethod
    def open(self, capital_token1: float, p_0: float) -> Position:
        """Construct the initial LP position from initial capital."""
        ...

    @abstractmethod
    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision: ...


# ---------- Concrete strategies ----------

@dataclass
class UnhedgedLP(Strategy):
    """Pure passive LP. No hedge, no rebalance. The HODL of LP-land."""
    name: str = "unhedged"
    profile: LpProfile = field(default_factory=lambda: UniformBandProfile(width_pct=0.10))

    def open(self, capital_token1: float, p_0: float) -> Position:
        return self.profile.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        return StepDecision()  # zero hedge, no rebalance


@dataclass
class NaiveDeltaHedge(Strategy):
    """Short the full LP delta every step. The v1 baseline that bled $5K."""
    name: str = "naive_delta"
    profile: LpProfile = field(default_factory=lambda: UniformBandProfile(width_pct=0.10))
    hedge_ratio: float = 1.0

    def open(self, capital_token1: float, p_0: float) -> Position:
        return self.profile.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        if state.position is None:
            return StepDecision()
        return StepDecision(
            target_short_token0=self.hedge_ratio * delta(state.position, p),
        )


@dataclass
class LQOptimalHedge(Strategy):
    """Bouchard-style LQ-optimal hedge ratio.

    Output ρ ∈ [0, 1] where ρ = γσ²T / (γσ²T + κ + γσ²_basis τ).
    Under realistic 1-day-horizon parameters, ρ → 0 (don't hedge daily).
    """
    name: str = "lq_optimal"
    profile: LpProfile = field(default_factory=lambda: UniformBandProfile(width_pct=0.10))
    sigma_basis: float = 0.05            # 5% annualized
    delay_seconds: float = 30.0
    risk_aversion: float = 5e-5
    txn_cost_coef: float = 100.0
    horizon_seconds: float = 86400.0

    def open(self, capital_token1: float, p_0: float) -> Position:
        return self.profile.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        if state.position is None:
            return StepDecision()
        params = HedgeParams(
            sigma=sigma,
            sigma_basis=self.sigma_basis,
            delay_years=self.delay_seconds / (365 * 24 * 3600),
            risk_aversion=self.risk_aversion,
            txn_cost_coef=self.txn_cost_coef,
            horizon_years=self.horizon_seconds / (365 * 24 * 3600),
        )
        rho = optimal_hedge_ratio(params)
        return StepDecision(
            target_short_token0=rho * delta(state.position, p),
        )


@dataclass
class SqueethGammaHedge(Strategy):
    """Long Squeeth to flatten γ; short residual Δ on a vanilla perp."""
    name: str = "squeeth_gamma"
    profile: LpProfile = field(default_factory=lambda: UniformBandProfile(width_pct=0.10))

    def open(self, capital_token1: float, p_0: float) -> Position:
        return self.profile.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        if state.position is None:
            return StepDecision()
        n_sqth = squeeth_size_to_flatten_gamma(state.position, p)
        residual_delta = delta(state.position, p) + 2.0 * n_sqth
        return StepDecision(
            target_short_token0=residual_delta,
            target_squeeth=n_sqth,
        )


@dataclass
class RebalanceOnTrigger(Strategy):
    """Rebalance the LP band when |delta-drift| exceeds a threshold.

    On open: build a uniform-band ±width centered at p_0.
    On step: if |price/p_open − 1| > rebalance_threshold, rebuild the band
    centered at the current price; restart hedging.
    """
    name: str = "rebalance_on_trigger"
    width_pct: float = 0.05
    rebalance_threshold: float = 0.05    # rebalance when 5% out of center
    hedge_ratio: float = 1.0

    def open(self, capital_token1: float, p_0: float) -> Position:
        prof = UniformBandProfile(width_pct=self.width_pct)
        return prof.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        if state.position is None:
            return StepDecision()
        # Center of current band:
        center = state.position.geometric_mean_price
        drift = abs(p / center - 1.0)
        if drift > self.rebalance_threshold:
            current_value = position_value(state.position, p)
            prof = UniformBandProfile(width_pct=self.width_pct)
            new_pos = prof.build(current_value, p)
            return StepDecision(
                target_short_token0=self.hedge_ratio * delta(new_pos, p),
                rebalance_to=new_pos,
            )
        return StepDecision(
            target_short_token0=self.hedge_ratio * delta(state.position, p),
        )


@dataclass
class FundingAwareHedge(Strategy):
    """LQ-optimal hedge scaled by realized funding rate.

    When funding > σ², the perp is expensive — pull the hedge down toward 0.
    When funding < 0, the perp pays the short — push hedge up.
    """
    name: str = "funding_aware"
    profile: LpProfile = field(default_factory=lambda: UniformBandProfile(width_pct=0.10))
    sigma_basis: float = 0.05
    funding_rate_apr: float = 0.10
    risk_aversion: float = 5e-5
    txn_cost_coef: float = 100.0

    def open(self, capital_token1: float, p_0: float) -> Position:
        return self.profile.build(capital_token1, p_0)

    def on_step(self, state: StrategyState, p: float, sigma: float,
                step_idx: int) -> StepDecision:
        if state.position is None:
            return StepDecision()
        params = HedgeParams(
            sigma=sigma,
            sigma_basis=self.sigma_basis,
            delay_years=30 / (365 * 24 * 3600),
            risk_aversion=self.risk_aversion,
            txn_cost_coef=self.txn_cost_coef,
            horizon_years=1 / 365.0,
        )
        rho = funding_aware_hedge_ratio(params, self.funding_rate_apr)
        return StepDecision(
            target_short_token0=rho * delta(state.position, p),
        )


# ---------- Strategy registry ----------
STRATEGIES = {
    "unhedged": UnhedgedLP,
    "naive_delta": NaiveDeltaHedge,
    "lq_optimal": LQOptimalHedge,
    "squeeth_gamma": SqueethGammaHedge,
    "rebalance_on_trigger": RebalanceOnTrigger,
    "funding_aware": FundingAwareHedge,
}


def get_strategy(name: str, **kwargs) -> Strategy:
    """Factory: get_strategy('lq_optimal', sigma_basis=0.03) → LQOptimalHedge(sigma_basis=0.03)."""
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy: {name}; known: {list(STRATEGIES)}")
    return STRATEGIES[name](**kwargs)
