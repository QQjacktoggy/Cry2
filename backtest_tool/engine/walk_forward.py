"""Walk-forward analysis: rolling train/test window optimization.

Validates that strategy parameters optimized on training data
continue to work on unseen test data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

if TYPE_CHECKING:
    from backtest_tool.engine.param_scanner import ParamScanner
    from backtest_tool.strategies.base_vbt import BaseVBTStrategy

logger = structlog.get_logger(__name__)


@dataclass
class WalkForwardResult:
    """Results from a walk-forward analysis."""

    windows: list[dict[str, Any]] = field(default_factory=list)
    train_sharpe_mean: float = 0.0
    test_sharpe_mean: float = 0.0
    sharpe_degradation: float = 0.0  # (train - test) / train
    oos_sharpe_positive: bool = False
    summary: dict[str, Any] = field(default_factory=dict)


class WalkForwardAnalyzer:
    """Perform rolling walk-forward analysis for a strategy.

    Usage:
        wfa = WalkForwardAnalyzer(train_months=12, test_months=3, step_months=3)
        result = wfa.run(strategy_cls, ohlcv, param_space, scanner)
    """

    def __init__(
        self,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 3,
        top_n_params: int = 3,
        target_metric: str = "sharpe_ratio",
    ) -> None:
        """Initialize walk-forward analyzer.

        Args:
            train_months: Training window length in months.
            test_months: Test window length in months.
            step_months: Step size between windows in months.
            top_n_params: Number of top params from train scan to test.
            target_metric: Metric to optimize during training.
        """
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.top_n_params = top_n_params
        self.target_metric = target_metric

    def _generate_windows(
        self, index: pd.DatetimeIndex
    ) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """Generate train/test window pairs.

        Args:
            index: DatetimeIndex of the full OHLCV data.

        Returns:
            List of (train_start, train_end, test_start, test_end) tuples.
        """
        if len(index) == 0:
            return []

        start = index[0]
        end = index[-1]
        windows = []

        train_delta = pd.DateOffset(months=self.train_months)
        test_delta = pd.DateOffset(months=self.test_months)
        step_delta = pd.DateOffset(months=self.step_months)

        current = start
        while True:
            train_start = current
            train_end = current + train_delta
            test_start = train_end
            test_end = test_start + test_delta

            if test_end > end:
                break

            windows.append((train_start, train_end, test_start, test_end))
            current = current + step_delta

        return windows

    def run(
        self,
        strategy_cls: type[BaseVBTStrategy],
        ohlcv: pd.DataFrame,
        param_space: dict[str, list],
        scanner: ParamScanner,
        initial_capital: float = 10000.0,
    ) -> WalkForwardResult:
        """Run walk-forward analysis.

        Args:
            strategy_cls: Strategy class to test.
            ohlcv: Full OHLCV DataFrame.
            param_space: Parameter space for training scan.
            scanner: ParamScanner instance.
            initial_capital: Starting capital.

        Returns:
            WalkForwardResult with per-window metrics.
        """
        windows = self._generate_windows(ohlcv.index)

        if not windows:
            logger.warning("Not enough data for walk-forward windows")
            return WalkForwardResult()

        logger.info(
            "Walk-forward analysis starting",
            strategy=strategy_cls.name if hasattr(strategy_cls, "name") else str(strategy_cls),
            total_windows=len(windows),
            train_months=self.train_months,
            test_months=self.test_months,
        )

        window_results = []
        train_sharpes = []
        test_sharpes = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            train_data = ohlcv.loc[train_start:train_end]
            test_data = ohlcv.loc[test_start:test_end]

            if len(train_data) < 30 or len(test_data) < 10:
                logger.debug(
                    "Skipping window: insufficient data",
                    window=i + 1,
                    train_bars=len(train_data),
                    test_bars=len(test_data),
                )
                continue

            try:
                train_results = scanner.scan(
                    strategy_cls=strategy_cls,
                    ohlcv=train_data,
                    param_space=param_space,
                    target_metric=self.target_metric,
                    top_n=self.top_n_params,
                    initial_capital=initial_capital,
                )

                if not train_results:
                    continue

                best_params = train_results[0]["params"]
                best_train_sharpe = train_results[0].get(self.target_metric, 0.0)

                test_strategy = strategy_cls(params=best_params)
                test_strategy_inst = test_strategy

                from backtest_tool.engine.runner import BacktestRunner

                runner = BacktestRunner()
                runner.initial_capital = initial_capital
                test_result = runner.run_single(test_strategy_inst, test_data)
                test_sharpe = test_result.metrics.get("sharpe_ratio", 0.0)

                window_record = {
                    "window": i + 1,
                    "train_start": str(train_start.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "best_params": best_params,
                    "train_sharpe": float(best_train_sharpe),
                    "test_sharpe": float(test_sharpe),
                    "train_bars": len(train_data),
                    "test_bars": len(test_data),
                }

                window_results.append(window_record)
                train_sharpes.append(float(best_train_sharpe))
                test_sharpes.append(float(test_sharpe))

                logger.info(
                    "Walk-forward window %d/%d",
                    i + 1, len(windows),
                    train_sharpe=f"{best_train_sharpe:.4f}",
                    test_sharpe=f"{test_sharpe:.4f}",
                )

            except Exception as e:
                logger.warning("Walk-forward window failed", window=i + 1, error=str(e))

        if not window_results:
            return WalkForwardResult(windows=[])

        train_sharpe_mean = float(sum(train_sharpes) / len(train_sharpes))
        test_sharpe_mean = float(sum(test_sharpes) / len(test_sharpes))

        if abs(train_sharpe_mean) > 1e-8:
            degradation = (train_sharpe_mean - test_sharpe_mean) / abs(train_sharpe_mean)
        else:
            degradation = 0.0

        result = WalkForwardResult(
            windows=window_results,
            train_sharpe_mean=train_sharpe_mean,
            test_sharpe_mean=test_sharpe_mean,
            sharpe_degradation=degradation,
            oos_sharpe_positive=test_sharpe_mean > 0,
            summary={
                "total_windows": len(window_results),
                "train_sharpe_mean": train_sharpe_mean,
                "test_sharpe_mean": test_sharpe_mean,
                "sharpe_degradation_pct": degradation * 100,
                "oos_positive": test_sharpe_mean > 0,
                "windows_oos_positive": sum(1 for ts in test_sharpes if ts > 0),
            },
        )

        logger.info(
            "Walk-forward analysis complete",
            windows=len(window_results),
            train_sharpe=f"{train_sharpe_mean:.4f}",
            test_sharpe=f"{test_sharpe_mean:.4f}",
            degradation_pct=f"{degradation * 100:.1f}%",
            oos_positive=result.oos_sharpe_positive,
        )

        return result
