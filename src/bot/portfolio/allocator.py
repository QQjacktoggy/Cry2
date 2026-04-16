"""Capital allocation across strategies.

Each strategy gets an independent virtual capital pool.
Pools are isolated - one strategy's losses don't affect another's allocation.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class CapitalAllocator:
    """Manages capital allocation across multiple strategies."""

    def __init__(
        self,
        total_capital: float,
        allocations: dict[str, float] | None = None,
    ) -> None:
        """Initialize capital allocator.

        Args:
            total_capital: Total account capital.
            allocations: Strategy name → allocation fraction (must sum to ≤ 1.0).
        """
        self.total_capital = total_capital
        self._allocations: dict[str, float] = allocations or {}
        self._strategy_capital: dict[str, float] = {}
        self._strategy_pnl: dict[str, float] = {}

        self._validate_allocations()
        self._initialize_capital()

    def _validate_allocations(self) -> None:
        """Validate that allocations don't exceed 100%."""
        total = sum(self._allocations.values())
        if total > 1.0 + 1e-9:
            logger.warning(
                "allocation_exceeds_100pct",
                total=total,
                allocations=self._allocations,
            )

    def _initialize_capital(self) -> None:
        """Initialize capital for each strategy based on allocations."""
        for strategy, fraction in self._allocations.items():
            self._strategy_capital[strategy] = self.total_capital * fraction
            self._strategy_pnl[strategy] = 0.0

        logger.info(
            "capital_allocated",
            total=self.total_capital,
            allocations=self._strategy_capital,
        )

    def get_capital(self, strategy_name: str) -> float:
        """Get current allocated capital for a strategy.

        Returns initial allocation + accumulated PnL.
        """
        base = self._strategy_capital.get(strategy_name, 0.0)
        pnl = self._strategy_pnl.get(strategy_name, 0.0)
        return base + pnl

    def update_pnl(self, strategy_name: str, pnl: float) -> None:
        """Update PnL for a strategy."""
        if strategy_name not in self._strategy_pnl:
            self._strategy_pnl[strategy_name] = 0.0
        self._strategy_pnl[strategy_name] += pnl

    def get_allocation_fraction(self, strategy_name: str) -> float:
        """Get the allocation fraction for a strategy."""
        return self._allocations.get(strategy_name, 0.0)

    def rebalance(self, new_total: float | None = None) -> None:
        """Rebalance allocations based on current equity.

        Args:
            new_total: New total capital. If None, uses current sum.
        """
        if new_total is not None:
            self.total_capital = new_total

        for strategy, fraction in self._allocations.items():
            new_capital = self.total_capital * fraction
            old_capital = self._strategy_capital.get(strategy, 0.0)
            self._strategy_capital[strategy] = new_capital
            logger.debug(
                "rebalanced",
                strategy=strategy,
                old=old_capital,
                new=new_capital,
            )

    def get_summary(self) -> dict[str, Any]:
        """Get allocation summary."""
        summary: dict[str, Any] = {
            "total_capital": self.total_capital,
            "strategies": {},
        }
        for strategy in self._allocations:
            summary["strategies"][strategy] = {
                "allocation_pct": self._allocations[strategy] * 100,
                "base_capital": self._strategy_capital.get(strategy, 0.0),
                "pnl": self._strategy_pnl.get(strategy, 0.0),
                "current_capital": self.get_capital(strategy),
            }
        return summary
