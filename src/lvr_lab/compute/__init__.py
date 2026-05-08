"""Pure-math kernels — no I/O, no external state. Tested against closed-form references."""

from ..domain.position import Position
from ..domain.portfolio import Portfolio, PortfolioGreeks
from .greeks import (
    position_value,
    position_amounts,
    delta,
    gamma,
    impermanent_loss,
    speed,
    lvr_vega,
    dollar_gamma,
    value_curve,
    delta_curve,
    gamma_curve,
    portfolio_value_curve,
    portfolio_delta_curve,
    portfolio_gamma_curve,
    time_in_range,
    expected_capital_efficiency,
    il_term_structure,
)
from .lvr import (
    lvr_rate_in_range,
    lvr_integrated,
    marginal_liquidity,
)
from .sigma_fee import sigma_fee_closed_form, sigma_fee_solve
from .vol_estimators import (
    realized_vol_close_to_close,
    realized_vol_parkinson,
    realized_vol_garman_klass,
    realized_vol_rogers_satchell,
    realized_vol_yang_zhang,
)
from .bootstrap import block_bootstrap_ci

__all__ = [
    "Position", "Portfolio", "PortfolioGreeks",
    "position_value",
    "position_amounts",
    "delta",
    "gamma",
    "impermanent_loss",
    "speed", "lvr_vega", "dollar_gamma",
    "value_curve", "delta_curve", "gamma_curve",
    "portfolio_value_curve", "portfolio_delta_curve", "portfolio_gamma_curve",
    "time_in_range", "expected_capital_efficiency", "il_term_structure",
    "lvr_rate_in_range",
    "lvr_integrated",
    "marginal_liquidity",
    "sigma_fee_closed_form",
    "sigma_fee_solve",
    "realized_vol_close_to_close",
    "realized_vol_parkinson",
    "realized_vol_garman_klass",
    "realized_vol_rogers_satchell",
    "realized_vol_yang_zhang",
    "block_bootstrap_ci",
]
