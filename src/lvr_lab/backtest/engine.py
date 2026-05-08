"""
Backtest engine — runs a Strategy on a price path with fee + LVR + funding accounting.

Designed to replace the ad-hoc `scripts/run_vault_backtest_v2.py`. The engine
is parameterized; strategies are pluggable; results are dataclass-typed.

Walk-forward and parameter-sweep utilities included.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Sequence, Optional, Callable

import numpy as np

from ..domain.position import Position
from ..compute.greeks import position_value, delta as delta_fn
from ..compute.lvr import lvr_rate_in_range
from ..compute.squeeth import squeeth_pnl_step
from .strategies import Strategy, StrategyState, StepDecision
from .metrics import full_report, PerformanceReport


@dataclass
class MarketStep:
    """One time step of market state the engine operates on."""
    timestamp: float        # unix seconds
    price: float            # token1/token0
    sigma_realized: float   # rolling YZ σ at this step (annualized)
    daily_volume_token1: float = 0.0  # for fee accrual modeling


@dataclass
class BacktestConfig:
    initial_capital_token1: float = 100_000.0
    fee_rate_bps: float = 5.0           # Ekubo USDC/ETH 5bp
    share_of_range: float = 0.30        # vault is X% of in-range liquidity
    funding_rate_apr: float = 0.10      # vanilla perp funding
    squeeth_funding_apr_func: Callable[[float], float] = field(
        default=lambda sigma: max(0.05, 2 * sigma ** 2)
    )
    annualization_periods: float = 365.0


@dataclass
class BacktestResult:
    strategy_name: str
    nav_series: np.ndarray
    short_history: np.ndarray
    sqth_history: np.ndarray
    cum_fees: float
    cum_lvr: float
    cum_perp_pnl: float
    cum_perp_funding: float
    cum_sqth_pnl: float
    cum_sqth_funding: float
    n_rebalances: int
    metrics: PerformanceReport


class BacktestEngine:
    """Run a Strategy over a sequence of MarketSteps."""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run(self, strategy: Strategy, market_path: Sequence[MarketStep]) -> BacktestResult:
        cfg = self.config
        if not market_path:
            raise ValueError("market_path is empty")

        steps = list(market_path)
        p0 = steps[0].price
        position = strategy.open(cfg.initial_capital_token1, p0)
        state = StrategyState(position=position)

        # Accumulators
        cum_fees = 0.0
        cum_lvr = 0.0
        cum_perp_pnl = 0.0
        cum_perp_funding = 0.0
        cum_sqth_pnl = 0.0
        cum_sqth_funding = 0.0
        nav_series: list[float] = []
        short_hist: list[float] = []
        sqth_hist: list[float] = []
        n_rebal = 0

        prev_p = p0
        for i, step in enumerate(steps):
            p = step.price
            sigma = step.sigma_realized

            # 1. Strategy decides target hedge.
            decision = strategy.on_step(state, p, sigma, i)

            # 2. Mark perp short to market over [prev_p, p].
            if i > 0:
                cum_perp_pnl += state.short_eth * (prev_p - p)
                # Funding paid over the period.
                notional = abs(state.short_eth) * 0.5 * (prev_p + p)
                cum_perp_funding += notional * cfg.funding_rate_apr / cfg.annualization_periods
                # Squeeth mark-to-market.
                if state.n_squeeth != 0:
                    sp, sf = squeeth_pnl_step(
                        state.n_squeeth, prev_p, p,
                        cfg.squeeth_funding_apr_func(sigma),
                        86400.0,
                    )
                    cum_sqth_pnl += sp
                    cum_sqth_funding += sf

            # 3. Apply rebalance if requested.
            if decision.rebalance_to is not None:
                state.position = decision.rebalance_to
                state.rebalance_count += 1
                n_rebal += 1

            # 4. Update hedge sizes.
            state.short_eth = decision.target_short_token0
            state.n_squeeth = decision.target_squeeth

            # 5. Fee accrual: only if in-range.
            if state.position is not None and state.position.in_range(p):
                fee_per_period = (
                    step.daily_volume_token1
                    * cfg.fee_rate_bps / 1e4
                    * cfg.share_of_range
                )
                cum_fees += fee_per_period

            # 6. Analytical LVR for accounting (the σ-implied baseline).
            if state.position is not None and state.position.in_range(p) and i > 0:
                r0 = lvr_rate_in_range(state.position, prev_p, sigma)
                r1 = lvr_rate_in_range(state.position, p, sigma)
                cum_lvr += 0.5 * (r0 + r1) / cfg.annualization_periods

            # 7. NAV
            v_lp = position_value(state.position, p) if state.position else 0.0
            nav = (
                v_lp + cum_fees
                + cum_perp_pnl - cum_perp_funding
                + cum_sqth_pnl - cum_sqth_funding
            )
            nav_series.append(nav)
            short_hist.append(state.short_eth)
            sqth_hist.append(state.n_squeeth)
            prev_p = p

        nav_arr = np.asarray(nav_series, dtype=float)
        return BacktestResult(
            strategy_name=strategy.name,
            nav_series=nav_arr,
            short_history=np.asarray(short_hist, dtype=float),
            sqth_history=np.asarray(sqth_hist, dtype=float),
            cum_fees=cum_fees,
            cum_lvr=cum_lvr,
            cum_perp_pnl=cum_perp_pnl,
            cum_perp_funding=cum_perp_funding,
            cum_sqth_pnl=cum_sqth_pnl,
            cum_sqth_funding=cum_sqth_funding,
            n_rebalances=n_rebal,
            metrics=full_report(nav_arr, cfg.annualization_periods),
        )

    # ---------- Walk-forward ----------
    def walk_forward(
        self,
        strategy_factory: Callable[[], Strategy],
        market_path: Sequence[MarketStep],
        train_window: int,
        test_window: int,
        step: int,
    ) -> list[BacktestResult]:
        """Walk-forward backtest.

        For each (train, test) split, instantiate a fresh strategy, train on
        the train window (no-op for stateless strategies), then run on test.
        Useful for detecting strategies that overfit the parameter grid.
        """
        results: list[BacktestResult] = []
        n = len(market_path)
        i = 0
        while i + train_window + test_window <= n:
            test_path = market_path[i + train_window: i + train_window + test_window]
            strategy = strategy_factory()
            results.append(self.run(strategy, test_path))
            i += step
        return results

    # ---------- Parameter sweep ----------
    def sweep(
        self,
        strategy_factory: Callable[..., Strategy],
        param_grid: dict,
        market_path: Sequence[MarketStep],
    ) -> list[tuple[dict, BacktestResult]]:
        """Cartesian-product sweep over a small parameter grid.

        param_grid: {"sigma_basis": [0.03, 0.05, 0.10], "delay_seconds": [10, 30, 60]}
        Returns: list of (params_dict, result).
        """
        from itertools import product
        keys = list(param_grid.keys())
        values = [param_grid[k] for k in keys]
        out = []
        for combo in product(*values):
            params = dict(zip(keys, combo))
            strategy = strategy_factory(**params)
            result = self.run(strategy, market_path)
            out.append((params, result))
        return out
