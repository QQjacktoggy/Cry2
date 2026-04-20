"""Backtest engine: runner, parameter scanning, portfolio optimization, risk management."""

from backtest_tool.engine.cost_model import CostModel
from backtest_tool.engine.param_scanner import ParamScanner
from backtest_tool.engine.portfolio_optimizer import PortfolioOptimizer
from backtest_tool.engine.risk_manager import (
    PositionSizer,
    RiskManager,
    apply_consecutive_loss_filter,
    apply_max_hold_limit,
    apply_portfolio_stop,
    compute_adaptive_leverage,
)
from backtest_tool.engine.runner import BacktestResult, BacktestRunner, MultiBacktestResult

__all__ = [
    "CostModel",
    "BacktestRunner",
    "BacktestResult",
    "MultiBacktestResult",
    "ParamScanner",
    "PortfolioOptimizer",
    "PositionSizer",
    "RiskManager",
    "apply_consecutive_loss_filter",
    "apply_max_hold_limit",
    "apply_portfolio_stop",
    "compute_adaptive_leverage",
]