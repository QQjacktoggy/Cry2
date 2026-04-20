"""Backtest engine: runner, parameter scanning, portfolio optimization, risk management, regime detection, analytics, robustness."""

from backtest_tool.engine.analytics import (
    compute_correlation_matrix,
    compute_correlation_summary,
    compute_multi_window_sharpe,
    compute_rolling_sharpe,
    compute_strategy_health_report,
    detect_strategy_decay,
    find_low_correlation_pairs,
)
from backtest_tool.engine.cost_model import CostModel
from backtest_tool.engine.param_scanner import ParamScanner
from backtest_tool.engine.portfolio_optimizer import PortfolioOptimizer
from backtest_tool.engine.regime import (
    MarketRegimeDetector,
    Regime,
    RegimeConfig,
    StrategySelector,
)
from backtest_tool.engine.risk_manager import (
    PositionSizer,
    RiskManager,
    apply_consecutive_loss_filter,
    apply_max_hold_limit,
    apply_portfolio_stop,
    compute_adaptive_leverage,
)
from backtest_tool.engine.robustness import (
    fee_sensitivity_analysis,
    generate_robustness_report,
    monte_carlo_simulation,
    optimize_multi_objective,
    parameter_stability_analysis,
    stress_test,
    walk_forward_analysis,
)
from backtest_tool.engine.runner import BacktestResult, BacktestRunner, MultiBacktestResult

__all__ = [
    # Core
    "CostModel",
    "BacktestRunner",
    "BacktestResult",
    "MultiBacktestResult",
    "ParamScanner",
    "PortfolioOptimizer",
    # Risk management (Phase 3)
    "PositionSizer",
    "RiskManager",
    "apply_consecutive_loss_filter",
    "apply_max_hold_limit",
    "apply_portfolio_stop",
    "compute_adaptive_leverage",
    # Regime detection (Phase 4A-4B)
    "MarketRegimeDetector",
    "Regime",
    "RegimeConfig",
    "StrategySelector",
    # Analytics (Phase 4C-4D)
    "compute_correlation_matrix",
    "compute_correlation_summary",
    "compute_multi_window_sharpe",
    "compute_rolling_sharpe",
    "compute_strategy_health_report",
    "detect_strategy_decay",
    "find_low_correlation_pairs",
    # Robustness (Phase 5)
    "fee_sensitivity_analysis",
    "generate_robustness_report",
    "monte_carlo_simulation",
    "optimize_multi_objective",
    "parameter_stability_analysis",
    "stress_test",
    "walk_forward_analysis",
]