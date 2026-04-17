"""Market regime detector for adapting strategy selection.

Classifies each bar into one of four market environments:
- trending_up:   ADX > 25 and EMA50 > EMA200
- trending_down: ADX > 25 and EMA50 < EMA200
- ranging:       ADX < 20
- volatile:      ATR percentile > 80
"""

from __future__ import annotations

import pandas as pd
import structlog

from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr_percentile
from backtest_tool.strategies.indicators.bollinger import compute_ema

logger = structlog.get_logger(__name__)

REGIME_LABELS = ("trending_up", "trending_down", "ranging", "volatile")


class MarketRegimeDetector:
    """Classify market bars into one of four regimes.

    Usage:
        detector = MarketRegimeDetector()
        regime = detector.classify(ohlcv)
        # regime is a pd.Series with string labels
    """

    def __init__(
        self,
        adx_period: int = 14,
        adx_trend_threshold: float = 25.0,
        adx_range_threshold: float = 20.0,
        ema_fast: int = 50,
        ema_slow: int = 200,
        atr_period: int = 14,
        atr_lookback: int = 100,
        atr_vol_percentile: float = 80.0,
    ) -> None:
        """Initialize regime detector.

        Args:
            adx_period: Period for ADX calculation.
            adx_trend_threshold: ADX above this → trending.
            adx_range_threshold: ADX below this → ranging.
            ema_fast: Fast EMA period for trend direction.
            ema_slow: Slow EMA period for trend direction.
            atr_period: Period for ATR calculation.
            atr_lookback: Rolling window for ATR percentile.
            atr_vol_percentile: ATR percentile above this → volatile.
        """
        self.adx_period = adx_period
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_range_threshold = adx_range_threshold
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_lookback = atr_lookback
        self.atr_vol_percentile = atr_vol_percentile

    def classify(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Classify each bar into a market regime.

        Priority order (when multiple conditions met):
        1. volatile (ATR > 80th pct) — highest priority
        2. trending_up (ADX > 25 and EMA50 > EMA200)
        3. trending_down (ADX > 25 and EMA50 < EMA200)
        4. ranging (default)

        Args:
            ohlcv: DataFrame with high, low, close columns and DatetimeIndex.

        Returns:
            Series of strings: 'trending_up', 'trending_down', 'ranging', 'volatile'.
        """
        adx = compute_adx(
            ohlcv["high"], ohlcv["low"], ohlcv["close"], self.adx_period
        )
        ema_fast = compute_ema(ohlcv["close"], self.ema_fast)
        ema_slow = compute_ema(ohlcv["close"], self.ema_slow)

        vol_bool = compute_atr_percentile(
            ohlcv["high"],
            ohlcv["low"],
            ohlcv["close"],
            atr_period=self.atr_period,
            lookback=self.atr_lookback,
            percentile=self.atr_vol_percentile,
        )

        trending_up = (adx > self.adx_trend_threshold) & (ema_fast > ema_slow)
        trending_down = (adx > self.adx_trend_threshold) & (ema_fast < ema_slow)

        regime = pd.Series("ranging", index=ohlcv.index, dtype=str)
        regime[trending_down] = "trending_down"
        regime[trending_up] = "trending_up"
        regime[vol_bool] = "volatile"

        counts = regime.value_counts()
        logger.info(
            "Market regime classification complete",
            total_bars=len(ohlcv),
            trending_up=int(counts.get("trending_up", 0)),
            trending_down=int(counts.get("trending_down", 0)),
            ranging=int(counts.get("ranging", 0)),
            volatile=int(counts.get("volatile", 0)),
        )

        return regime

    def regime_stats(self, ohlcv: pd.DataFrame) -> dict[str, float]:
        """Compute regime distribution statistics.

        Args:
            ohlcv: DataFrame with OHLCV data.

        Returns:
            Dict of regime label → fraction of bars.
        """
        regime = self.classify(ohlcv)
        total = len(regime)
        if total == 0:
            return {label: 0.0 for label in REGIME_LABELS}

        counts = regime.value_counts()
        return {label: float(counts.get(label, 0)) / total for label in REGIME_LABELS}
