"""Backtest engine: runner, parameter scanning, portfolio optimization, risk management, regime detection, analytics, robustness."""

try:
    from backtest_tool.engine.analytics import (
        compute_correlation_matrix,
        compute_correlation_summary,
        compute_multi_window_sharpe,
        compute_rolling_sharpe,
        compute_strategy_health_report,
        detect_strategy_decay,
        find_low_correlation_pairs,
    )
except ImportError:
    compute_correlation_matrix = None
    compute_correlation_summary = None
    compute_multi_window_sharpe = None
    compute_rolling_sharpe = None
    compute_strategy_health_report = None
    detect_strategy_decay = None
    find_low_correlation_pairs = None

from backtest_tool.engine.cost_model import CostModel
try:
    from backtest_tool.engine.param_scanner import ParamScanner
    from backtest_tool.engine.portfolio_optimizer import PortfolioOptimizer
    from backtest_tool.engine.runner import BacktestResult, BacktestRunner, MultiBacktestResult
except ImportError:
    ParamScanner = None
    PortfolioOptimizer = None
    BacktestResult = None
    BacktestRunner = None
    MultiBacktestResult = None
try:
    from backtest_tool.engine.regime import (
        MarketRegimeDetector,
        Regime,
        RegimeConfig,
        StrategySelector,
    )
except ImportError:
    MarketRegimeDetector = None
    Regime = None
    RegimeConfig = None
    StrategySelector = None
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