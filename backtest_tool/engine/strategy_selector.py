"""Strategy selector: choose strategies based on market regime.

Maps detected market regimes to suitable strategies.
Smoothing prevents rapid switching between regimes.
"""

from __future__ import annotations

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Default regime → strategies mapping
DEFAULT_REGIME_MAP: dict[str, list[str]] = {
    "trending_up": ["trend_donchian"],
    "trending_down": ["trend_donchian", "funding_arb"],
    "ranging": ["grid_futures", "mean_reversion_bb"],
    "volatile": [],  # No new entries in volatile regimes
}


class StrategySelector:
    """Select active strategies based on market regime.

    Usage:
        selector = StrategySelector(min_regime_bars=6)
        mask = selector.generate_entry_mask(regime_series, "trend_donchian")
        filtered_entries = entries & mask
    """

    def __init__(
        self,
        regime_map: dict[str, list[str]] | None = None,
        min_regime_bars: int = 6,
    ) -> None:
        """Initialize strategy selector.

        Args:
            regime_map: Custom mapping of regime → strategy names.
            min_regime_bars: Minimum consecutive bars in a regime before switching.
                             Prevents whipsaw switching.
        """
        self.regime_map = regime_map or DEFAULT_REGIME_MAP
        self.min_regime_bars = min_regime_bars

    def smooth_regime(self, regime: pd.Series) -> pd.Series:
        """Apply smoothing to reduce regime switching noise.

        A regime change is only accepted if the new regime persists
        for at least min_regime_bars consecutive bars.

        Args:
            regime: Raw regime Series from MarketRegimeDetector.

        Returns:
            Smoothed regime Series.
        """
        if len(regime) == 0:
            return regime

        smoothed = regime.copy()
        current_regime = regime.iloc[0]
        run_start = 0

        for i in range(1, len(regime)):
            if regime.iloc[i] != current_regime:
                run_length = i - run_start
                if run_length < self.min_regime_bars:
                    smoothed.iloc[run_start:i] = current_regime
                else:
                    current_regime = regime.iloc[i]
                    run_start = i

        return smoothed

    def generate_entry_mask(
        self,
        regime: pd.Series,
        strategy_name: str,
        smooth: bool = True,
    ) -> pd.Series:
        """Generate boolean mask for when a strategy should be active.

        Args:
            regime: Regime Series (raw or smoothed).
            strategy_name: Name of the strategy to check.
            smooth: Whether to apply regime smoothing first.

        Returns:
            Boolean Series (True = strategy is allowed to trade).
        """
        if smooth:
            regime = self.smooth_regime(regime)

        active_regimes = [
            r for r, strats in self.regime_map.items()
            if strategy_name in strats
        ]

        if not active_regimes:
            return pd.Series(False, index=regime.index)

        mask = regime.isin(active_regimes)

        logger.info(
            "Strategy entry mask generated",
            strategy=strategy_name,
            active_regimes=active_regimes,
            bars_allowed=int(mask.sum()),
            total_bars=len(mask),
        )

        return mask

    def select_strategies(
        self,
        regime: pd.Series,
        available_strategies: list[str],
        smooth: bool = True,
    ) -> dict[str, pd.Series]:
        """Generate entry masks for all available strategies.

        Args:
            regime: Regime Series.
            available_strategies: List of strategy names to consider.
            smooth: Whether to apply regime smoothing.

        Returns:
            Dict mapping strategy name → boolean mask Series.
        """
        if smooth:
            regime = self.smooth_regime(regime)

        return {
            name: self.generate_entry_mask(regime, name, smooth=False)
            for name in available_strategies
        }
