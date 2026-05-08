"""
Per-fill markouts — the practitioner workhorse for measuring adverse selection.

For each Swap event with execution price p_swap at time t, the markout at horizon h
is (p_ref(t+h) − p_swap), signed by trade direction. Negative markouts on buys
(or positive on sells) indicate the LP got hit by informed flow.

Implementation operates on a list of Swap rows joined to a time-indexed reference
price series (CEX mid). Linear interpolation between reference quotes at sub-second
horizons; nearest-neighbor at longer horizons.
"""

from __future__ import annotations
import bisect
from dataclasses import dataclass
from typing import Sequence, List, Dict


@dataclass(frozen=True)
class Swap:
    timestamp: float          # unix seconds
    pool_price: float         # token1/token0 at fill
    size_token0: float        # signed; positive = LP sold token0 (trader bought)
    pool_id: str = ""


@dataclass(frozen=True)
class ReferenceQuote:
    timestamp: float
    mid: float                # reference mid price


def _interp_ref(refs: Sequence[ReferenceQuote], t: float) -> float:
    """Linearly interpolate the reference mid at time t; clamp at boundaries."""
    if not refs:
        return float("nan")
    times = [r.timestamp for r in refs]
    idx = bisect.bisect_left(times, t)
    if idx == 0:
        return refs[0].mid
    if idx >= len(refs):
        return refs[-1].mid
    r0, r1 = refs[idx - 1], refs[idx]
    if r1.timestamp == r0.timestamp:
        return r1.mid
    w = (t - r0.timestamp) / (r1.timestamp - r0.timestamp)
    return r0.mid + w * (r1.mid - r0.mid)


def markout(swap: Swap, refs: Sequence[ReferenceQuote], horizon_seconds: float) -> float:
    """Markout = signed (p_ref(t+h) − p_swap) · |size_token0|.

    Positive value = LP made money on this fill at this horizon.
    Sign convention: when size_token0 > 0 the LP sold token0 (received token1),
    so a *lower* p_ref(t+h) than p_swap is favorable → flip sign.
    """
    p_ref = _interp_ref(refs, swap.timestamp + horizon_seconds)
    if not (p_ref == p_ref):  # NaN guard
        return float("nan")
    direction = -1.0 if swap.size_token0 > 0 else 1.0
    return direction * (p_ref - swap.pool_price) * abs(swap.size_token0)


def markout_panel(swaps: Sequence[Swap], refs: Sequence[ReferenceQuote],
                  horizons_seconds: Sequence[float] = (1, 5, 30, 300)) -> List[Dict[str, float]]:
    """Return a list of per-swap markouts at each horizon."""
    out = []
    for s in swaps:
        row = {"timestamp": s.timestamp, "pool_id": s.pool_id, "pool_price": s.pool_price}
        for h in horizons_seconds:
            row[f"markout_{int(h)}s"] = markout(s, refs, h)
        out.append(row)
    return out
