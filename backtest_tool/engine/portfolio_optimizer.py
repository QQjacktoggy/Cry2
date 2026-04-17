"""PortfolioOptimizer: Multi-strategy capital allocation optimization.

Finds the optimal allocation weights across multiple strategies
to maximize a target metric (e.g., Sharpe ratio).
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pandas as pd
import structlog

from backtest_tool.engine.runner import BacktestResult

logger = structlog.get_logger(__name__)


class PortfolioOptimizer:
    """Optimizes capital allocation across multiple strategies."""

    def optimize(
        self,
        strategy_results: dict[str, BacktestResult],
        allocation_step: float = 0.05,
        min_allocation: float = 0.0,
        max_allocation: float = 0.80,
        target_metric: str = "sharpe_ratio",
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Find optimal capital allocation across strategies.

        Uses brute-force grid search with specified step size.
        All allocations must sum to 1.0.

        Args:
            strategy_results: Dict mapping strategy name to BacktestResult.
            allocation_step: Step size for allocation weights (e.g., 0.05 = 5%).
            min_allocation: Minimum allocation per strategy.
            max_allocation: Maximum allocation per strategy.
            target_metric: Metric to optimize (default: sharpe_ratio).
            top_n: Number of top allocations to return.

        Returns:
            Dict with 'best_allocation', 'top_allocations', and 'summary'.
        """
        strategy_names = list(strategy_results.keys())
        n_strategies = len(strategy_names)

        if n_strategies == 0:
            return {"best_allocation": {}, "top_allocations": [], "summary": {}}

        if n_strategies == 1:
            name = strategy_names[0]
            return {
                "best_allocation": {name: 1.0},
                "top_allocations": [{"allocation": {name: 1.0}, target_metric: strategy_results[name].metrics.get(target_metric, 0)}],
                "summary": {"combinations_tested": 1},
            }

        # Extract equity curves
        equities: dict[str, pd.Series] = {}
        for name, result in strategy_results.items():
            eq = result.equity_curve
            if not eq.empty:
                # Normalize to return series (percentage change from initial)
                equities[name] = eq / eq.iloc[0]

        if not equities:
            return {"best_allocation": {}, "top_allocations": [], "summary": {}}

        # Generate allocation combinations that sum to 1.0
        allocations = self._generate_allocations(
            n_strategies, allocation_step, min_allocation, max_allocation
        )

        logger.info(
            "Starting portfolio optimization",
            strategies=strategy_names,
            combinations=len(allocations),
            step=allocation_step,
            target=target_metric,
        )

        # Evaluate each allocation
        results: list[dict[str, Any]] = []
        for alloc_weights in allocations:
            alloc_dict = dict(zip(strategy_names, alloc_weights))

            # Compute combined equity
            combined = self._combine_equity(equities, alloc_dict)
            if combined is None or len(combined) < 2:
                continue

            # Compute metrics
            metrics = self._compute_metrics(combined)
            metric_val = metrics.get(target_metric, 0.0)

            results.append({
                "allocation": alloc_dict,
                **metrics,
            })

        if not results:
            return {"best_allocation": {}, "top_allocations": [], "summary": {}}

        # Sort by target metric
        results.sort(key=lambda x: x.get(target_metric, 0), reverse=True)

        top_results = results[:top_n]
        best = top_results[0]

        logger.info(
            "Portfolio optimization complete",
            best_allocation=best["allocation"],
            best_metric=best.get(target_metric, 0),
            combinations_tested=len(allocations),
        )

        return {
            "best_allocation": best["allocation"],
            "top_allocations": top_results,
            "summary": {
                "combinations_tested": len(allocations),
                "target_metric": target_metric,
                "best_value": best.get(target_metric, 0),
            },
        }

    @staticmethod
    def _generate_allocations(
        n: int,
        step: float,
        min_alloc: float,
        max_alloc: float,
    ) -> list[tuple[float, ...]]:
        """Generate all allocation combinations that sum to 1.0.

        Args:
            n: Number of strategies.
            step: Allocation step size.
            min_alloc: Minimum per-strategy allocation.
            max_alloc: Maximum per-strategy allocation.

        Returns:
            List of allocation tuples.
        """
        # Generate possible values for each strategy
        possible = []
        val = min_alloc
        while val <= max_alloc + 1e-10:
            possible.append(round(val, 4))
            val += step

        # Cartesian product, filter to sum == 1.0
        valid: list[tuple[float, ...]] = []
        for combo in itertools.product(possible, repeat=n):
            if abs(sum(combo) - 1.0) < 1e-6:
                valid.append(combo)

        return valid

    @staticmethod
    def _combine_equity(
        equities: dict[str, pd.Series],
        allocations: dict[str, float],
    ) -> pd.Series | None:
        """Combine normalized equity curves with allocation weights.

        Args:
            equities: Dict of normalized equity curves (start at 1.0).
            allocations: Dict of allocation weights.

        Returns:
            Combined equity Series, or None if empty.
        """
        combined = None
        for name, weight in allocations.items():
            if name not in equities or weight <= 0:
                continue
            eq = equities[name] * weight
            if combined is None:
                combined = eq.copy()
            else:
                combined, eq = combined.align(eq, fill_value=0)
                combined = combined + eq

        return combined

    @staticmethod
    def _compute_metrics(equity: pd.Series) -> dict[str, float]:
        """Compute performance metrics from an equity curve.

        Args:
            equity: Combined equity Series (normalized).

        Returns:
            Dict of metric name → value.
        """
        metrics: dict[str, float] = {}

        returns = equity.pct_change().dropna()
        if len(returns) < 2:
            return metrics

        metrics["total_return"] = float((equity.iloc[-1] / equity.iloc[0]) - 1)

        total_days = (equity.index[-1] - equity.index[0]).days
        if total_days > 0:
            metrics["annualized_return"] = (1 + metrics["total_return"]) ** (365.0 / total_days) - 1
        else:
            metrics["annualized_return"] = 0.0

        # Sharpe ratio (assume risk-free = 0)
        hours_per_bar = max((equity.index[1] - equity.index[0]).total_seconds() / 3600, 0.0167)
        ann_factor = np.sqrt(365 * 24 / hours_per_bar)

        if returns.std() > 0:
            metrics["sharpe_ratio"] = float(returns.mean() / returns.std() * ann_factor)
        else:
            metrics["sharpe_ratio"] = 0.0

        # Sortino ratio
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            metrics["sortino_ratio"] = float(returns.mean() / downside.std() * ann_factor)
        else:
            metrics["sortino_ratio"] = 0.0

        # Max drawdown
        cummax = equity.cummax()
        drawdown = (equity - cummax) / cummax
        metrics["max_drawdown"] = float(drawdown.min())

        # Volatility
        metrics["volatility_ann"] = float(returns.std() * ann_factor)

        # Calmar
        if metrics["max_drawdown"] != 0:
            metrics["calmar_ratio"] = metrics["annualized_return"] / abs(metrics["max_drawdown"])
        else:
            metrics["calmar_ratio"] = 0.0

        return metrics
