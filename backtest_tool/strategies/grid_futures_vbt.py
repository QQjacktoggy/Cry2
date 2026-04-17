"""Strategy B: Grid Futures with Trend Filter (VectorBT).

Simplified vectorized grid trading:
- Compute 20-day price range as grid channel.
- EMA200 trend filter: bullish = long only, bearish = short only, ranging = both.
- Buy in lower half of channel, sell in upper half.

Reference: src/bot/strategy/grid_futures.py (event-driven version)
Note: Exact grid-level tracking is inherently stateful and cannot be perfectly
vectorized. This version approximates grid behavior using channel zones.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.bollinger import compute_ema


class GridFuturesVBT(BaseVBTStrategy):
    """Grid trading strategy for futures with EMA trend filter (VectorBT)."""

    name = "grid_futures"
    default_params: dict[str, Any] = {
        "grid_count": 20,
        "range_period": 20,
        "ema_period": 200,
        "leverage": 2,
        "dynamic_grid": False,
        "grid_atr_period": 14,
    }
    required_timeframe = "4h"

    def _compute_channel(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Compute price channel from rolling high/low range.

        Args:
            ohlcv: DataFrame with OHLCV data.

        Returns:
            Tuple of (upper, lower, midline) Series.
            All shifted by 1 to avoid look-ahead bias.
        """
        period = self.params["range_period"]
        upper = ohlcv["high"].rolling(period).max().shift(1) * 1.02
        lower = ohlcv["low"].rolling(period).min().shift(1) * 0.98
        midline = (upper + lower) / 2.0
        return upper, lower, midline

    def _compute_trend(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Compute trend direction using EMA.

        Args:
            ohlcv: DataFrame with OHLCV data.

        Returns:
            Series with values: 1 (bullish), -1 (bearish), 0 (ranging).
        """
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        close = ohlcv["close"]

        # Bullish: close > EMA * 1.01, Bearish: close < EMA * 0.99, else ranging
        trend = pd.Series(0, index=ohlcv.index, dtype=int)
        trend[close > ema * 1.01] = 1
        trend[close < ema * 0.99] = -1
        return trend

    def _compute_zone_fractions(self, ohlcv: pd.DataFrame) -> tuple[float, float]:
        """Compute dynamic buy/sell zone fractions based on ATR percentile.

        High ATR → wider zones (buy at 25%, sell at 75%) → fewer trades
        Low ATR  → narrower zones (buy at 40%, sell at 60%) → more trades

        Returns:
            Tuple of (buy_zone_fraction, sell_zone_fraction).
        """
        if not self.params.get("dynamic_grid", False):
            return 0.33, 0.67

        from backtest_tool.strategies.indicators.atr import compute_atr_percentile

        vol_high = compute_atr_percentile(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            atr_period=self.params.get("grid_atr_period", 14),
            lookback=100,
            percentile=80.0,
        )

        high_vol_ratio = float(vol_high.mean()) if not vol_high.empty else 0.5

        buy_frac = 0.25 + (1 - high_vol_ratio) * 0.15
        sell_frac = 0.75 - (1 - high_vol_ratio) * 0.15
        buy_frac = max(0.10, min(0.45, buy_frac))
        sell_frac = max(0.55, min(0.90, sell_frac))

        return buy_frac, sell_frac

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals: price in lower channel zone + bullish/ranging.

        Buy when price drops to lower portion of the grid channel.
        Only active when trend is bullish or ranging.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long entry signals.
        """
        ohlcv = ohlcv.copy()
        upper, lower, midline = self._compute_channel(ohlcv)
        trend = self._compute_trend(ohlcv)
        close = ohlcv["close"]

        # Buy zone: lower third of channel
        buy_frac, sell_frac = self._compute_zone_fractions(ohlcv)
        buy_zone_top = lower + (upper - lower) * buy_frac
        in_buy_zone = close <= buy_zone_top

        # Trend filter: only long in bullish or ranging
        trend_ok = trend >= 0

        entries = in_buy_zone & trend_ok & upper.notna()
        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals: price in upper channel zone.

        Sell when price rises to upper portion of the grid channel.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long exit signals.
        """
        ohlcv = ohlcv.copy()
        upper, lower, midline = self._compute_channel(ohlcv)
        close = ohlcv["close"]

        # Sell zone: upper third of channel
        _, sell_frac = self._compute_zone_fractions(ohlcv)
        sell_zone_bottom = lower + (upper - lower) * sell_frac
        exits = close >= sell_zone_bottom

        return exits.fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short entry signals: price in upper channel zone + bearish/ranging.

        Short when price rises to upper portion of the grid channel.
        Only active when trend is bearish or ranging.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short entry signals.
        """
        ohlcv = ohlcv.copy()
        upper, lower, midline = self._compute_channel(ohlcv)
        trend = self._compute_trend(ohlcv)
        close = ohlcv["close"]

        # Short zone: upper third of channel
        _, sell_frac = self._compute_zone_fractions(ohlcv)
        short_zone_bottom = lower + (upper - lower) * sell_frac
        in_short_zone = close >= short_zone_bottom

        # Trend filter: only short in bearish or ranging
        trend_ok = trend <= 0

        short_entries = in_short_zone & trend_ok & upper.notna()
        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short exit signals: price in lower channel zone.

        Cover shorts when price drops to lower portion of channel.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short exit signals.
        """
        ohlcv = ohlcv.copy()
        upper, lower, midline = self._compute_channel(ohlcv)
        close = ohlcv["close"]

        # Cover zone: lower third of channel
        buy_frac, _ = self._compute_zone_fractions(ohlcv)
        cover_zone_top = lower + (upper - lower) * buy_frac
        short_exits = close <= cover_zone_top

        return short_exits.fillna(False)
