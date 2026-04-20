"""Market Regime Detection and Strategy Selection.

Phase 4 implementation:
  4A: MarketRegimeDetector — ADX + EMA + ATR based regime classification
  4B: StrategySelector — Route strategies based on detected regime
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_ema


# =============================================================================
# 4A: Market Regime Detector
# =============================================================================


class Regime(str, Enum):
    """Market regime labels."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"


@dataclass
class RegimeConfig:
    """Configuration for regime detection thresholds."""

    # ADX thresholds
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    adx_range_threshold: float = 20.0

    # EMA for trend direction
    ema_period: int = 50

    # ATR volatility thresholds
    atr_period: int = 14
    atr_lookback: int = 100
    atr_volatile_percentile: float = 80.0

    # Smoothing: minimum bars in same regime before switching
    min_regime_bars: int = 3


class MarketRegimeDetector:
    """Classify market conditions into regimes.

    Classification logic (evaluated in priority order):
      1. VOLATILE: ATR > Nth percentile (dangerous, avoid trading)
      2. TRENDING_UP: ADX > threshold AND close > EMA
      3. TRENDING_DOWN: ADX > threshold AND close < EMA
      4. RANGING: everything else (ADX < range_threshold or between thresholds)
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()

    def detect(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Classify each bar into a market regime.

        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume].

        Returns:
            Series of regime string labels indexed like ohlcv.
            Values: 'trending_up', 'trending_down', 'ranging', 'volatile'.
        """
        cfg = self.config
        high, low, close = ohlcv["high"], ohlcv["low"], ohlcv["close"]

        # Compute indicators
        adx = compute_adx(high, low, close, cfg.adx_period)
        ema = compute_ema(close, cfg.ema_period)
        atr = compute_atr(high, low, close, cfg.atr_period)
        atr_pct = atr.rolling(cfg.atr_lookback).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

        # Fill NaN indicators (warmup bars default to safe values)
        adx = adx.fillna(0.0)
        ema = ema.fillna(close)
        atr_pct = atr_pct.fillna(0.5)

        # Build regime series using string labels (avoids pandas enum truncation)
        regime = pd.Series(Regime.RANGING.value, index=ohlcv.index, dtype="object")

        # Layer 1: Trending (ADX above trend threshold)
        trending = adx > cfg.adx_trend_threshold
        trend_up = trending & (close > ema)
        trend_down = trending & (close <= ema)
        regime.loc[trend_up] = Regime.TRENDING_UP.value
        regime.loc[trend_down] = Regime.TRENDING_DOWN.value

        # Layer 2: Volatile overrides trend (highest priority)
        volatile = atr_pct > (cfg.atr_volatile_percentile / 100.0)
        regime.loc[volatile] = Regime.VOLATILE.value

        # Smooth: prevent regime flickering
        if cfg.min_regime_bars > 1:
            regime = self._smooth_regimes(regime, cfg.min_regime_bars)

        return regime

    def detect_numeric(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Return regime as integer codes for vectorized operations.

        Mapping: trending_up=1, trending_down=2, ranging=3, volatile=4
        """
        regime = self.detect(ohlcv)
        mapping = {
            Regime.TRENDING_UP.value: 1,
            Regime.TRENDING_DOWN.value: 2,
            Regime.RANGING.value: 3,
            Regime.VOLATILE.value: 4,
        }
        return regime.map(mapping).astype(int)

    def regime_summary(self, regimes: pd.Series) -> dict[str, Any]:
        """Compute summary statistics for each regime.

        Returns:
            Dict with counts, percentages, and longest streak per regime.
        """
        total = len(regimes)
        summary: dict[str, Any] = {}

        for r in Regime:
            mask = regimes == r.value
            count = int(mask.sum())
            pct = count / total * 100 if total > 0 else 0.0

            # Longest consecutive streak
            streak = 0
            if count > 0:
                groups = (mask != mask.shift()).cumsum()
                streaks = mask.groupby(groups).sum()
                streak = int(streaks.max())

            summary[r.value] = {
                "count": count,
                "pct": round(pct, 1),
                "longest_streak": streak,
            }

        return summary

    @staticmethod
    def _smooth_regimes(regimes: pd.Series, min_bars: int) -> pd.Series:
        """Remove regime changes that last fewer than min_bars."""
        result = regimes.copy()
        groups = (regimes != regimes.shift()).cumsum()

        for gid in groups.unique():
            mask = groups == gid
            if mask.sum() < min_bars:
                # Replace short regime with previous regime
                first_idx = mask.idxmax()
                loc = regimes.index.get_loc(first_idx)
                if loc > 0:
                    prev_regime = result.iloc[loc - 1]
                    result[mask] = prev_regime

        return result


# =============================================================================
# 4B: Strategy Selector — regime-based strategy routing
# =============================================================================


# Default strategy routing map
DEFAULT_REGIME_MAP: dict[str, list[str]] = {
    Regime.TRENDING_UP.value: [
        "momentum_ranking",
        "trend_donchian",
        "trend_donchian_mtf",
        "trend_donchian_adx_slope",
    ],
    Regime.TRENDING_DOWN.value: [
        "momentum_ranking",
        "trend_donchian",
    ],
    Regime.RANGING.value: [
        "grid_trend_bias",
        "mean_reversion_bb",
        "breakout_squeeze",
        "grid_funding_aware",
    ],
    Regime.VOLATILE.value: [],  # No strategies — reduce exposure
}

# Position size multipliers per regime
DEFAULT_REGIME_SIZING: dict[str, float] = {
    Regime.TRENDING_UP.value: 1.0,
    Regime.TRENDING_DOWN.value: 0.7,
    Regime.RANGING.value: 0.8,
    Regime.VOLATILE.value: 0.3,  # 30% exposure in volatile
}


@dataclass
class StrategySelector:
    """Select and filter strategies based on detected market regime.

    The selector provides:
      1. Strategy name routing: which strategies should be active per regime
      2. Signal filtering: suppress entry signals during unfavourable regimes
      3. Position sizing multiplier: reduce exposure in risky regimes
    """

    regime_map: dict[str, list[str]] = field(
        default_factory=lambda: dict(DEFAULT_REGIME_MAP)
    )
    regime_sizing: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_REGIME_SIZING)
    )

    def select_strategies(self, regime: str | Regime) -> list[str]:
        """Return list of strategy names that should be active for a regime.

        Args:
            regime: Current market regime (string or Regime enum).

        Returns:
            List of strategy name strings.
        """
        key = regime.value if isinstance(regime, Regime) else regime
        return self.regime_map.get(key, [])

    def get_sizing_multiplier(self, regime: str | Regime) -> float:
        """Return position sizing multiplier for a regime.

        Args:
            regime: Current market regime.

        Returns:
            Float multiplier (0.0 to 1.0).
        """
        key = regime.value if isinstance(regime, Regime) else regime
        return self.regime_sizing.get(key, 1.0)

    def apply_regime_filter(
        self,
        entries: pd.Series,
        exits: pd.Series,
        regimes: pd.Series,
        allowed_regimes: list[str | Regime],
        short_entries: pd.Series | None = None,
        short_exits: pd.Series | None = None,
    ) -> tuple[pd.Series, pd.Series, pd.Series | None, pd.Series | None]:
        """Filter entry/exit signals to only fire during allowed regimes.

        New entries are suppressed in non-allowed regimes. Exits are always
        allowed (never block an exit signal).

        Args:
            entries: Long entry boolean signals.
            exits: Long exit boolean signals.
            regimes: Series of regime labels (same index as entries).
            allowed_regimes: List of regimes where entries are permitted.
            short_entries: Short entry signals (optional).
            short_exits: Short exit signals (optional).

        Returns:
            Filtered (entries, exits, short_entries, short_exits).
        """
        allowed_set = {
            r.value if isinstance(r, Regime) else r for r in allowed_regimes
        }
        regime_values = regimes.map(
            lambda x: x.value if isinstance(x, Regime) else x
        )
        allowed_mask = regime_values.isin(allowed_set)

        filtered_entries = entries & allowed_mask
        # Exits always pass through (never trap in a position)
        filtered_exits = exits.copy()

        filtered_short_entries = None
        filtered_short_exits = None
        if short_entries is not None:
            filtered_short_entries = short_entries & allowed_mask
        if short_exits is not None:
            filtered_short_exits = short_exits.copy()

        return (
            filtered_entries,
            filtered_exits,
            filtered_short_entries,
            filtered_short_exits,
        )

    def compute_regime_sizing_series(
        self,
        regimes: pd.Series,
    ) -> pd.Series:
        """Convert regime series to position sizing multiplier series.

        Args:
            regimes: Series of regime labels.

        Returns:
            Series of float multipliers (0.0–1.0).
        """
        regime_values = regimes.map(
            lambda x: x.value if isinstance(x, Regime) else x
        )
        return regime_values.map(self.regime_sizing).fillna(1.0).astype(float)
