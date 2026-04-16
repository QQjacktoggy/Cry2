"""Hyperparameter optimization using Optuna.

Runs backtests with different parameter combinations to find optimal settings.
Uses Bayesian optimization (TPE sampler) for efficient search.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


class StrategyOptimizer:
    """Optuna-based strategy parameter optimizer."""

    def __init__(
        self,
        backtest_fn: Callable[..., dict[str, Any]],
        objective_metric: str = "sharpe_ratio",
        n_trials: int = 100,
        direction: str = "maximize",
    ) -> None:
        """Initialize optimizer.

        Args:
            backtest_fn: Function that takes params dict and returns metrics dict.
            objective_metric: Metric to optimize.
            n_trials: Number of optimization trials.
            direction: 'maximize' or 'minimize'.
        """
        self.backtest_fn = backtest_fn
        self.objective_metric = objective_metric
        self.n_trials = n_trials
        self.direction = direction

    def optimize(
        self,
        param_space: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Run optimization.

        Args:
            param_space: Parameter search space definition.
                Format: {"param_name": {"type": "int|float|categorical", "low": x, "high": y}}

        Returns:
            Best parameters found.
        """
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.error("optuna_not_installed")
            return {}

        def objective(trial: optuna.Trial) -> float:
            params = {}
            for name, spec in param_space.items():
                if spec["type"] == "int":
                    params[name] = trial.suggest_int(name, spec["low"], spec["high"])
                elif spec["type"] == "float":
                    params[name] = trial.suggest_float(
                        name, spec["low"], spec["high"],
                        log=spec.get("log", False),
                    )
                elif spec["type"] == "categorical":
                    params[name] = trial.suggest_categorical(name, spec["choices"])

            try:
                metrics = self.backtest_fn(**params)
                value = metrics.get(self.objective_metric, 0.0)

                # Report intermediate values for pruning
                trial.report(value, step=0)

                return value
            except Exception as e:
                logger.warning("trial_failed", error=str(e))
                return float("-inf") if self.direction == "maximize" else float("inf")

        study = optuna.create_study(
            direction=self.direction,
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(),
        )

        logger.info("optimization_starting", trials=self.n_trials, metric=self.objective_metric)
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=True)

        best = study.best_params
        best_value = study.best_value

        logger.info(
            "optimization_complete",
            best_params=best,
            best_value=best_value,
            metric=self.objective_metric,
        )

        return {
            "best_params": best,
            "best_value": best_value,
            "n_trials": len(study.trials),
        }

    def save_results(self, results: dict[str, Any], output_path: str) -> None:
        """Save optimization results to JSON."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info("optimization_results_saved", path=output_path)
