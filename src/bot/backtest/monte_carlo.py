"""Monte Carlo simulation - shuffles trade order to assess robustness.

Randomly reorders trades ≥ 1000 times to build a distribution
of possible outcomes (equity curves, drawdowns).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class MonteCarloSimulator:
    """Monte Carlo simulation by trade resampling."""

    def __init__(self, num_simulations: int = 1000, seed: int | None = None) -> None:
        self.num_simulations = num_simulations
        self.rng = np.random.default_rng(seed)

    def run(
        self,
        trade_pnls: list[float],
        initial_capital: float = 10000.0,
    ) -> dict[str, Any]:
        """Run Monte Carlo simulation.

        Args:
            trade_pnls: List of per-trade PnL values.
            initial_capital: Starting capital.

        Returns:
            Statistics from the simulation.
        """
        if not trade_pnls:
            return self._empty_result()

        pnls = np.array(trade_pnls)
        n_trades = len(pnls)

        logger.info(
            "monte_carlo_starting",
            simulations=self.num_simulations,
            trades=n_trades,
        )

        final_equities = []
        max_drawdowns = []
        max_drawdown_pcts = []

        for _ in range(self.num_simulations):
            # Shuffle trade order
            shuffled = self.rng.permutation(pnls)
            equity_curve = initial_capital + np.cumsum(shuffled)

            # Calculate max drawdown
            peak = np.maximum.accumulate(equity_curve)
            drawdown = equity_curve - peak
            max_dd = float(drawdown.min())
            max_dd_pct = float((drawdown / peak).min()) if peak.max() > 0 else 0.0

            final_equities.append(float(equity_curve[-1]))
            max_drawdowns.append(max_dd)
            max_drawdown_pcts.append(max_dd_pct)

        final_eq = np.array(final_equities)
        max_dds = np.array(max_drawdown_pcts)

        final_equity_summary = {
            "mean": float(np.mean(final_eq)),
            "median": float(np.median(final_eq)),
            "std": float(np.std(final_eq)),
            "p5": float(np.percentile(final_eq, 5)),
            "p25": float(np.percentile(final_eq, 25)),
            "p75": float(np.percentile(final_eq, 75)),
            "p95": float(np.percentile(final_eq, 95)),
            "min": float(np.min(final_eq)),
            "max": float(np.max(final_eq)),
        }
        max_drawdown_summary = {
            "mean": float(np.mean(max_dds)),
            "median": float(np.median(max_dds)),
            "p5": float(np.percentile(max_dds, 5)),
            "p95": float(np.percentile(max_dds, 95)),
            "worst": float(np.min(max_dds)),
        }
        ruin_probability = float(np.mean(final_eq < initial_capital * 0.5))
        profit_probability = float(np.mean(final_eq > initial_capital))

        result = {
            "num_simulations": self.num_simulations,
            "num_trades": n_trades,
            "initial_capital": initial_capital,
            "final_equity": final_equity_summary,
            "max_drawdown_pct": max_drawdown_summary,
            "ruin_probability": ruin_probability,
            "profit_probability": profit_probability,
        }

        logger.info(
            "monte_carlo_complete",
            mean_equity=final_equity_summary["mean"],
            profit_prob=profit_probability,
            ruin_prob=ruin_probability,
        )

        return result

    def _empty_result(self) -> dict[str, Any]:
        """Return empty result when no trades."""
        return {
            "num_simulations": self.num_simulations,
            "num_trades": 0,
            "final_equity": {},
            "max_drawdown_pct": {},
            "ruin_probability": 0.0,
            "profit_probability": 0.0,
        }
