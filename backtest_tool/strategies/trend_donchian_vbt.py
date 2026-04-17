"""Strategy C: Donchian Breakout Trend Following (VectorBT).

Entry: Close > Donchian Upper (entry_period) AND ADX > threshold → Long
       Close < Donchian Lower (entry_period) AND ADX > threshold → Short
Exit:  Close < Donchian Lower (exit_period) for longs
       Close > Donchian Upper (exit_period) for shorts

Reference: src/bot/strategy/trend_donchian.py (event-driven version)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.donchian import compute_donchian


class TrendDonchianVBT(BaseVBTStrategy):
    """Donchian Breakout trend following strategy for VectorBT."""

    name = "trend_donchian"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "adx_period": 14,
        "adx_threshold": 25,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "leverage": 2,
    }
    required_timeframe = "4h"

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals: Close > Donchian Upper AND ADX > threshold.

        Steps:
        1. Compute Donchian Upper (entry_period), shifted by 1 to avoid look-ahead.
        2. Compute ADX.
        3. entries = (close > donchian_upper) & (adx > adx_threshold)

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long entry signals.
        """
        ohlcv = ohlcv.copy()
        entry_upper, _, _ = compute_donchian(
            ohlcv["high"], ohlcv["low"], self.params["entry_period"]
        )
        adx = compute_adx(
            ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"]
        )

        entries = (ohlcv["close"] > entry_upper) & (adx > self.params["adx_threshold"])
        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals: Close < Donchian Lower (exit_period).

        Uses Donchian exit channel as the primary exit mechanism.
        ATR trailing stop is approximated by the Donchian exit channel.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long exit signals.
        """
        ohlcv = ohlcv.copy()
        _, exit_lower, _ = compute_donchian(
            ohlcv["high"], ohlcv["low"], self.params["exit_period"]
        )

        exits = ohlcv["close"] < exit_lower
        return exits.fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short entry signals: Close < Donchian Lower AND ADX > threshold.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short entry signals.
        """
        ohlcv = ohlcv.copy()
        _, entry_lower, _ = compute_donchian(
            ohlcv["high"], ohlcv["low"], self.params["entry_period"]
        )
        adx = compute_adx(
            ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"]
        )

        short_entries = (ohlcv["close"] < entry_lower) & (adx > self.params["adx_threshold"])
        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short exit signals: Close > Donchian Upper (exit_period).

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short exit signals.
        """
        ohlcv = ohlcv.copy()
        exit_upper, _, _ = compute_donchian(
            ohlcv["high"], ohlcv["low"], self.params["exit_period"]
        )

        short_exits = ohlcv["close"] > exit_upper
        return short_exits.fillna(False)
