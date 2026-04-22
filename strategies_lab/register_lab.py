"""Independent registry for strategies_lab.

Call `register_lab_strategies()` explicitly to add lab prototypes to the
StrategyRegistry. The main project's `register_default_strategies()` is
UNTOUCHED — this is opt-in.

Usage (from a scratch script or notebook):

    import sys
    sys.path.insert(0, "src")
    sys.path.insert(0, ".")

    from bot.strategy.registry import register_default_strategies
    from strategies_lab.register_lab import register_lab_strategies

    register_default_strategies()   # V7.2 main line
    register_lab_strategies()       # Opt-in lab additions
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def register_lab_strategies() -> None:
    """Register all lab prototypes. Opt-in only."""
    from bot.strategy.registry import StrategyRegistry

    from strategies_lab.btc_dominance_rotation import BtcDominanceRotationStrategy
    from strategies_lab.cross_exchange_funding import CrossExchangeFundingStrategy
    from strategies_lab.liquidation_hunter import LiquidationHunterStrategy
    from strategies_lab.perp_spot_basis_arb import PerpSpotBasisArbStrategy
    from strategies_lab.stat_arb_pairs import StatArbPairsStrategy

    StrategyRegistry.register("lab_perp_spot_basis", PerpSpotBasisArbStrategy)
    StrategyRegistry.register("lab_cross_exchange_funding", CrossExchangeFundingStrategy)
    StrategyRegistry.register("lab_stat_arb_pairs", StatArbPairsStrategy)
    StrategyRegistry.register("lab_liquidation_hunter", LiquidationHunterStrategy)
    StrategyRegistry.register("lab_btc_dominance_rotation", BtcDominanceRotationStrategy)

    logger.info("lab_strategies_registered", count=5)


__all__ = ["register_lab_strategies"]
