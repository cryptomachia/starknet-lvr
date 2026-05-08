"""
Synthetic LP simulator — given a price path and an LP profile, compute fees,
inventory P&L, IL, and the hedged return when paired with a perp short.

Two canonical profiles:
- uniform-band:    L spread evenly over [p·(1−w), p·(1+w)] for chosen width w
- concentrated-active: all L on the active tick (single-bin v3 equivalent)

Real on-chain positions are reconstructed from on-chain `Position*` events in
the swap-level pipeline (M1 of grant); this simulator is the analytical baseline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Sequence, Tuple, Dict, List
import math

from .greeks import Position, position_value, position_amounts, delta, impermanent_loss
from .lvr import lvr_rate_in_range


@dataclass
class SimResult:
    initial_value_token1: float
    final_value_token1: float
    fees_token1: float
    lvr_token1: float
    il_token1: float
    hedge_pnl_token1: float          # PnL from a short-perp hedge
    funding_paid_token1: float       # cumulative funding cost paid on the hedge
    net_hedged_return_token1: float
    timeseries: List[Dict[str, float]] = field(default_factory=list)


def make_uniform_band(p_ref: float, width_pct: float, value_usd: float) -> Position:
    """Uniform-band LP centered on p_ref with half-width width_pct (e.g., 0.05 = ±5%)."""
    p_a = p_ref * (1.0 - width_pct)
    p_b = p_ref * (1.0 + width_pct)
    coeff = 2 * math.sqrt(p_ref) - math.sqrt(p_a) - p_ref / math.sqrt(p_b)
    L = value_usd / coeff
    return Position(L=L, p_a=p_a, p_b=p_b)


def simulate(
    pos: Position,
    price_path: Sequence[Tuple[float, float]],   # (t_seconds, p) sorted
    fee_rate_bps: float,                         # pool fee per swap
    swap_volume_per_step_token1: float,          # avg per-step swap volume in token1
    sigma_for_lvr_proxy: float = 0.0,            # if > 0, also accumulate analytical LVR
    funding_rate_per_year: float = 0.0,          # perp funding cost (paid by short)
    hedge_ratio: float = 1.0,                    # 1.0 = full delta hedge of token0 inventory
) -> SimResult:
    """Approximate simulation:
    - Fees = volume_token1 × (fee_bps/1e4) per step (collected by LP, scaled by share-of-range — set to 1 if pool has only this LP).
    - LVR = ∫ lvr_rate dt with σ given (analytical baseline; empirical LVR uses
      bin-crossing accounting in the swap-level pipeline).
    - Hedge = short Δ(p_t) token0 against the LP; mark-to-market each step;
      pay funding on the absolute position size, time-weighted.
    """
    rows = list(price_path)
    rows.sort(key=lambda r: r[0])
    if not rows:
        return SimResult(0, 0, 0, 0, 0, 0, 0, 0)

    p0 = rows[0][1]
    v0 = position_value(pos, p0)

    fees = 0.0
    lvr = 0.0
    funding = 0.0
    hedge_pnl = 0.0
    prev_t, prev_p = rows[0]
    prev_delta = delta(pos, prev_p)
    timeseries = []

    for i, (t, p) in enumerate(rows[1:], start=1):
        dt = t - prev_t
        if dt <= 0:
            prev_t, prev_p = t, p
            continue

        # Fees — approximate as constant volume * fee_rate per step
        step_fee = swap_volume_per_step_token1 * fee_rate_bps / 1e4
        fees += step_fee

        # LVR — trapezoidal of rate over [prev_t, t]
        if sigma_for_lvr_proxy > 0:
            r0 = lvr_rate_in_range(pos, prev_p, sigma_for_lvr_proxy)
            r1 = lvr_rate_in_range(pos, p, sigma_for_lvr_proxy)
            lvr += 0.5 * (r0 + r1) * dt

        # Hedge: short prev_delta token0 from prev_t to t.
        # Hedge PnL (in token1) = +short_size · (prev_p − p)   (short profits when p falls)
        short_size = hedge_ratio * prev_delta
        hedge_pnl += short_size * (prev_p - p)

        # Funding: paid by short on |notional|. Notional in token1 = short_size · current_p.
        # Funding rate per year, dt in seconds.
        notional = abs(short_size) * 0.5 * (prev_p + p)
        funding += notional * funding_rate_per_year * (dt / (365 * 24 * 3600.0))

        timeseries.append({
            "t": t,
            "p": p,
            "value": position_value(pos, p),
            "fees_cum": fees,
            "lvr_cum": lvr,
            "hedge_pnl_cum": hedge_pnl,
            "funding_cum": funding,
            "delta": delta(pos, p),
        })

        prev_t, prev_p = t, p
        prev_delta = delta(pos, p)

    p_final = rows[-1][1]
    v_final = position_value(pos, p_final)
    il = impermanent_loss(pos, p_final, p0)
    net = (v_final - v0) + fees + hedge_pnl - funding

    return SimResult(
        initial_value_token1=v0,
        final_value_token1=v_final,
        fees_token1=fees,
        lvr_token1=lvr,
        il_token1=il,
        hedge_pnl_token1=hedge_pnl,
        funding_paid_token1=funding,
        net_hedged_return_token1=net,
        timeseries=timeseries,
    )
