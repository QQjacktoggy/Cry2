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
        buy_zone_top = lower + (upper - lower) * 0.33
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
        sell_zone_bottom = lower + (upper - lower) * 0.67
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
        short_zone_bottom = lower + (upper - lower) * 0.67
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
        cover_zone_top = lower + (upper - lower) * 0.33
        short_exits = close <= cover_zone_top

        return short_exits.fillna(False)
