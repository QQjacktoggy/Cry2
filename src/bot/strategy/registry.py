"""Strategy registry for dynamic strategy loading and management."""

from __future__ import annotations

from typing import Any

import structlog

from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class StrategyRegistry:
    """Registry for strategy classes. Supports dynamic loading."""

    _registry: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_class: type[BaseStrategy]) -> None:
        """Register a strategy class."""
        cls._registry[name] = strategy_class
        logger.info("strategy_registered", name=name)

    @classmethod
    def get(cls, name: str) -> type[BaseStrategy]:
        """Get a strategy class by name."""
        if name not in cls._registry:
            raise KeyError(f"Strategy '{name}' not registered. Available: {list(cls._registry.keys())}")
        return cls._registry[name]

    @classmethod
    def create(cls, name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
        """Create a strategy instance by name."""
        strategy_class = cls.get(name)
        return strategy_class(params=params)

    @classmethod
    def list_strategies(cls) -> list[str]:
        """List all registered strategy names."""
        return list(cls._registry.keys())

    @classmethod
    def create_all(
        cls,
        strategy_configs: dict[str, dict[str, Any]],
    ) -> list[BaseStrategy]:
        """Create all enabled strategies from config.

        Args:
            strategy_configs: Dict of strategy_name -> params from YAML.

        Returns:
            List of initialized strategy instances.
        """
        strategies = []
        for name, params in strategy_configs.items():
            if not params.get("enabled", True):
                logger.info("strategy_disabled", name=name)
                continue
            try:
                strategy = cls.create(name, params)
                strategies.append(strategy)
                logger.info("strategy_created", name=name, symbols=strategy.symbols)
            except KeyError:
                logger.warning("strategy_not_found", name=name)
        return strategies


def register_default_strategies() -> None:
    """Register all built-in strategies."""
    from bot.strategy.funding_arb import FundingArbStrategy
    from bot.strategy.grid_futures import GridFuturesStrategy
    from bot.strategy.mean_reversion_bb import MeanReversionBBStrategy
    from bot.strategy.trend_donchian import TrendDonchianStrategy

    StrategyRegistry.register("funding_arb", FundingArbStrategy)
    StrategyRegistry.register("grid_futures", GridFuturesStrategy)
    StrategyRegistry.register("trend_donchian", TrendDonchianStrategy)
    StrategyRegistry.register("mean_reversion_bb", MeanReversionBBStrategy)
