"""
Stress scenarios — what does the LP / vault lose in tail events?

Five canonical scenarios:
  - PriceShockUp:        ETH +50% in one block (governance unlock, ETF approval)
  - PriceShockDown:      ETH −50% (FTX-style failure)
  - PegBreak:            stablecoin depeg (USDC to $0.85)
  - VolSpike:            σ → 200% (geopolitical event, exchange failure)
  - LiquidityCrisis:     volume → 0, slippage spike, hedge venue down
"""

from __future__ import annotations
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..domain.position import Position
from ..compute.greeks import position_value, delta as delta_fn
from ..compute.lvr import lvr_rate_in_range


@dataclass
class StressResult:
    scenario_name: str
    pre_value: float
    post_value: float
    delta_value: float
    delta_pct: float
    notes: str = ""


class StressScenario(ABC):
    """Apply a stress to a Position; report the value change."""

    @abstractmethod
    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult: ...


@dataclass
class PriceShockUp(StressScenario):
    """ETH spikes up by `magnitude` (e.g., 0.5 = +50%)."""
    magnitude: float = 0.5

    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult:
        v_pre = position_value(pos, p_pre)
        p_post = p_pre * (1 + self.magnitude)
        v_post = position_value(pos, p_post)
        return StressResult(
            scenario_name=f"PriceShockUp(+{self.magnitude*100:.0f}%)",
            pre_value=v_pre, post_value=v_post,
            delta_value=v_post - v_pre,
            delta_pct=(v_post - v_pre) / v_pre if v_pre > 0 else float("nan"),
            notes=("LP under-performs HODL when price moves; "
                   "magnitude depends on range width."),
        )


@dataclass
class PriceShockDown(StressScenario):
    magnitude: float = 0.5

    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult:
        v_pre = position_value(pos, p_pre)
        p_post = p_pre * (1 - self.magnitude)
        v_post = position_value(pos, p_post)
        return StressResult(
            scenario_name=f"PriceShockDown(−{self.magnitude*100:.0f}%)",
            pre_value=v_pre, post_value=v_post,
            delta_value=v_post - v_pre,
            delta_pct=(v_post - v_pre) / v_pre if v_pre > 0 else float("nan"),
        )


@dataclass
class PegBreak(StressScenario):
    """Stablecoin depeg: token1 drops to `peg_target` of its peg.

    For a USDC/USDT pool, this models USDT → $0.85. Effectively a price
    shock on the quote leg.
    """
    peg_target: float = 0.85    # 0.85 = -15% depeg

    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult:
        v_pre = position_value(pos, p_pre)
        # If peg breaks downward, p_pre rises (more token1 needed per token0)
        p_post = p_pre / self.peg_target if self.peg_target > 0 else p_pre
        v_post = position_value(pos, p_post)
        return StressResult(
            scenario_name=f"PegBreak({self.peg_target:.2f})",
            pre_value=v_pre, post_value=v_post,
            delta_value=v_post - v_pre,
            delta_pct=(v_post - v_pre) / v_pre if v_pre > 0 else float("nan"),
            notes="LP is forced to absorb the depegged side.",
        )


@dataclass
class VolSpike(StressScenario):
    """σ → `target_sigma` for `duration_days`. Reports expected LVR loss."""
    target_sigma: float = 2.0       # 200% annualized
    duration_days: float = 7.0

    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult:
        v_pre = position_value(pos, p_pre)
        # Expected LVR over duration at target σ
        rate = lvr_rate_in_range(pos, p_pre, self.target_sigma)
        expected_lvr = rate * (self.duration_days / 365.0)
        v_post = v_pre - expected_lvr
        return StressResult(
            scenario_name=f"VolSpike(σ→{self.target_sigma*100:.0f}%, {self.duration_days}d)",
            pre_value=v_pre, post_value=v_post,
            delta_value=-expected_lvr,
            delta_pct=-expected_lvr / v_pre if v_pre > 0 else float("nan"),
            notes=f"Assumes price stays in range; LVR rate scales as σ². "
                  f"Pre-σ {sigma_pre*100:.0f}% → post-σ {self.target_sigma*100:.0f}% "
                  f"⇒ {self.target_sigma**2 / sigma_pre**2:.1f}× LVR rate.",
        )


@dataclass
class LiquidityCrisis(StressScenario):
    """Volume collapses to a tiny fraction; hedge venue may be down."""
    volume_collapse_pct: float = 0.95     # 95% volume drop
    hedge_unavailable: bool = True

    def apply(self, pos: Position, p_pre: float, sigma_pre: float = 0.4) -> StressResult:
        v_pre = position_value(pos, p_pre)
        # Crude model: LP earns 5% of normal fees but bears full LVR + hedge funding cost
        # if hedge is unavailable. Assume 1-week duration.
        duration_days = 7.0
        normal_daily_fees = v_pre * 0.05 / 365  # 5% APR baseline
        crisis_fees = normal_daily_fees * (1 - self.volume_collapse_pct) * duration_days
        rate = lvr_rate_in_range(pos, p_pre, sigma_pre * 1.5)  # σ usually rises in crisis
        expected_lvr = rate * (duration_days / 365.0)
        funding_drag = v_pre * 0.20 * duration_days / 365 if self.hedge_unavailable else 0
        v_post = v_pre + crisis_fees - expected_lvr - funding_drag
        return StressResult(
            scenario_name="LiquidityCrisis",
            pre_value=v_pre, post_value=v_post,
            delta_value=v_post - v_pre,
            delta_pct=(v_post - v_pre) / v_pre if v_pre > 0 else float("nan"),
            notes=f"Volume −{self.volume_collapse_pct*100:.0f}%, "
                  f"σ +50%, hedge {'unavailable' if self.hedge_unavailable else 'OK'}.",
        )


def run_stress_book(pos: Position, p: float, sigma: float = 0.4) -> dict:
    """Run a canonical stress book and return a dict of results."""
    scenarios = {
        "shock_up_50":   PriceShockUp(0.50),
        "shock_up_30":   PriceShockUp(0.30),
        "shock_down_50": PriceShockDown(0.50),
        "shock_down_30": PriceShockDown(0.30),
        "peg_break_15":  PegBreak(0.85),
        "vol_spike_2x":  VolSpike(target_sigma=2.0, duration_days=7),
        "liquidity_crisis": LiquidityCrisis(),
    }
    out = {}
    for name, scenario in scenarios.items():
        out[name] = scenario.apply(pos, p, sigma_pre=sigma)
    return out
