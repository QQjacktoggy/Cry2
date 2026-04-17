"""ParamScanner: Parameter space scanning for strategy optimization.

Supports Cartesian product parameter sweeps with result ranking.
"""

from __future__ import annotations

import itertools
from typing import Any

import pandas as pd
import structlog
from tqdm import tqdm

from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.strategies.base_vbt import BaseVBTStrategy

logger = structlog.get_logger(__name__)


class ParamScanner:
    """Scans parameter spaces for optimal strategy configurations."""

    def __init__(self, runner: BacktestRunner) -> None:
        """Initialize scanner with a BacktestRunner.

        Args:
            runner: BacktestRunner instance for executing backtests.
        """
        self.runner = runner

    def scan(
        self,
        strategy_class: type[BaseVBTStrategy],
        param_space: dict[str, list],
        ohlcv: pd.DataFrame,
        sort_by: str = "sharpe_ratio",
        top_n: int = 20,
    ) -> pd.DataFrame:
        """Scan parameter space and rank results.

        Generates all Cartesian product combinations, runs backtest for each,
        and returns results sorted by the specified metric.

        Args:
            strategy_class: Strategy class to instantiate with each param set.
            param_space: Dict mapping param names to lists of values.
            ohlcv: OHLCV DataFrame for backtesting.
            sort_by: Metric name to sort by (descending).
            top_n: Number of top results to return.

        Returns:
            DataFrame with params and metrics for top_n results.
        """
        combinations = self._generate_combinations(param_space)
        total = len(combinations)

        logger.info(
            "Starting parameter scan",
            strategy=strategy_class.name,
            combinations=total,
            sort_by=sort_by,
            top_n=top_n,
        )

        results: list[dict[str, Any]] = []

        for params in tqdm(combinations, desc=f"Scanning {strategy_class.name}"):
            try:
                strategy = strategy_class(params=params)
                bt_result = self.runner.run_single(strategy, ohlcv)

                row = {**params, **bt_result.metrics}
                results.append(row)
            except Exception as e:
                logger.debug("Param combo failed", params=params, error=str(e))
                continue

        if not results:
            logger.warning("No successful results from parameter scan")
            return pd.DataFrame()

        df = pd.DataFrame(results)

        # Sort by target metric (descending for positive-is-better metrics)
        descending = sort_by not in ("max_drawdown",)
        if sort_by in df.columns:
            df = df.sort_values(sort_by, ascending=not descending)
        else:
            logger.warning("Sort metric '%s' not found in results", sort_by)

        df = df.head(top_n).reset_index(drop=True)
        df.index = df.index + 1  # 1-based ranking
        df.index.name = "rank"

        logger.info(
            "Parameter scan complete",
            total_combos=total,
            successful=len(results),
            top_n=len(df),
        )

        return df

    @staticmethod
    def _generate_combinations(param_space: dict[str, list]) -> list[dict]:
        """Generate Cartesian product of parameter space.

        Args:
            param_space: Dict mapping param names to lists of values.

        Returns:
            List of parameter dicts.
        """
        if not param_space:
            return [{}]

        keys = list(param_space.keys())
        values = list(param_space.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]
