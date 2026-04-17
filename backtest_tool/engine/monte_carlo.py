"""Monte Carlo simulation by shuffling trade PnL sequences.

Quantifies the role of luck in backtest results by simulating 1000 random
orderings of the same trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

if TYPE_CHECKING:
    from backtest_tool.engine.runner import BacktestResult

logger = structlog.get_logger(__name__)


@dataclass
class MonteCarloResult:
    """Results from a Monte Carlo simulation."""

    n_simulations: int = 0
    final_values: np.ndarray = field(default_factory=lambda: np.array([]))
    max_drawdowns: np.ndarray = field(default_factory=lambda: np.array([]))
    percentiles: dict[str, float] = field(default_factory=dict)
    actual_final_value: float = 0.0
    actual_max_drawdown: float = 0.0
    actual_percentile_rank: float = 0.0


class MonteCarloSimulator:
    """Simulate return distributions by shuffling trade PnL.

    Usage:
        sim = MonteCarloSimulator(n_simulations=1000)
        result = sim.run(backtest_result)
    """

    def __init__(
        self,
        n_simulations: int = 1000,
        initial_capital: float | None = None,
        seed: int | None = 42,
    ) -> None:
        """Initialize Monte Carlo simulator.

        Args:
            n_simulations: Number of random shuffles.
            initial_capital: Starting capital (overrides result's value).
            seed: Random seed for reproducibility.
        """
        self.n_simulations = n_simulations
        self.initial_capital = initial_capital
        self.rng = np.random.default_rng(seed)

    def run(self, result: BacktestResult) -> MonteCarloResult:
        """Run Monte Carlo simulation on backtest result.

        Args:
            result: BacktestResult containing trade PnL data.

        Returns:
            MonteCarloResult with distribution statistics.
        """
        trades = result.trades
        if trades.empty:
            logger.warning("No trades found, returning empty Monte Carlo result")
            return MonteCarloResult(n_simulations=0)

        pnl_col = "PnL" if "PnL" in trades.columns else None
        if pnl_col is None:
            for col in trades.columns:
                if "pnl" in col.lower() or "profit" in col.lower():
                    pnl_col = col
                    break

        if pnl_col is None:
            logger.warning("Could not find PnL column in trades")
            return MonteCarloResult(n_simulations=0)

        pnl_array = trades[pnl_col].dropna().to_numpy()
        if len(pnl_array) == 0:
            return MonteCarloResult(n_simulations=0)

        initial_cap = self.initial_capital or result.metrics.get("initial_capital", 10000.0)
        actual_final = float(result.equity_curve.iloc[-1]) if len(result.equity_curve) > 0 else initial_cap
        actual_dd = result.metrics.get("max_drawdown", 0.0)

        final_values = np.empty(self.n_simulations)
        max_drawdowns = np.empty(self.n_simulations)

        for i in range(self.n_simulations):
            shuffled = self.rng.permutation(pnl_array)
            equity = initial_cap + np.cumsum(shuffled)
            equity = np.insert(equity, 0, initial_cap)

            final_values[i] = equity[-1]
            cummax = np.maximum.accumulate(equity)
            dd = (equity - cummax) / np.where(cummax > 1e-8, cummax, 1e-8)
            max_drawdowns[i] = float(np.min(dd))

        pct_labels = [5, 25, 50, 75, 95]
        percentiles = {
            f"final_value_p{p}": float(np.percentile(final_values, p))
            for p in pct_labels
        }
        percentiles.update({
            f"max_dd_p{p}": float(np.percentile(max_drawdowns, p))
            for p in pct_labels
        })

        actual_rank = float(np.mean(final_values <= actual_final)) * 100

        logger.info(
            "Monte Carlo simulation complete",
            n_simulations=self.n_simulations,
            median_final_value=f"{percentiles['final_value_p50']:.2f}",
            median_max_dd=f"{percentiles['max_dd_p50']:.4f}",
            actual_rank_pct=f"{actual_rank:.1f}%",
        )

        return MonteCarloResult(
            n_simulations=self.n_simulations,
            final_values=final_values,
            max_drawdowns=max_drawdowns,
            percentiles=percentiles,
            actual_final_value=actual_final,
            actual_max_drawdown=actual_dd,
            actual_percentile_rank=actual_rank,
        )
