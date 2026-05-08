"""Risk engine — VaR, stress tests, correlation/factor decomposition, P&L attribution."""

from .var import (
    historical_var, gaussian_var, cornish_fisher_var,
    cvar_expected_shortfall, var_decomposition,
)
from .stress import (
    StressScenario, StressResult,
    PriceShockUp, PriceShockDown, PegBreak, VolSpike, LiquidityCrisis,
    run_stress_book,
)
from .correlation import (
    sample_covariance, ledoit_wolf_shrinkage,
    correlation_from_covariance, factor_decomposition,
    hierarchical_risk_parity,
)
from .attribution import (
    AttributionRow, AttributionReport,
    attribute_pnl, factor_attribution,
)

__all__ = [
    "historical_var", "gaussian_var", "cornish_fisher_var",
    "cvar_expected_shortfall", "var_decomposition",
    "StressScenario", "StressResult",
    "PriceShockUp", "PriceShockDown", "PegBreak", "VolSpike", "LiquidityCrisis",
    "run_stress_book",
    "sample_covariance", "ledoit_wolf_shrinkage",
    "correlation_from_covariance", "factor_decomposition",
    "hierarchical_risk_parity",
    "AttributionRow", "AttributionReport",
    "attribute_pnl", "factor_attribution",
]
