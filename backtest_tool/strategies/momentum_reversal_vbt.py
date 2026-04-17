"""Strategy E: Momentum Reversal (VectorBT).

Contrarian strategy:
- Go long when price has fallen the most over N days (oversold + reversal expected)
- Go short when price has risen the most over N days (overbought + reversal expected)
- Uses RSI confirmation for entry timing.

Best suited for 1D timeframe.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.bollinger import compute_rsi


class MomentumReversalVBT(BaseVBTStrategy):
    """Momentum reversal (contrarian) strategy for VectorBT."""

    name = "momentum_reversal"
    default_params: dict[str, Any] = {
        "lookback": 14,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "return_threshold": -5.0,  # % return below which we go long
        "leverage": 1,
    }
    required_timeframe = "1d"

    def _compute_returns(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Compute N-day rolling return.

        Args:
            ohlcv: DataFrame with close column.

        Returns:
            Series of N-day percentage returns.
        """
        lookback = self.params["lookback"]
        returns = ohlcv["close"].pct_change(lookback) * 100
        return returns

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals: N-day return below threshold AND RSI oversold.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long entry signals.
        """
        ohlcv = ohlcv.copy()
        returns = self._compute_returns(ohlcv)
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        threshold = self.params["return_threshold"]
        entries = (returns < threshold) & (rsi < self.params["rsi_oversold"])
        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals: N-day return turns positive OR RSI overbought.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long exit signals.
        """
        ohlcv = ohlcv.copy()
        returns = self._compute_returns(ohlcv)
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        exits = (returns > 0) | (rsi > self.params["rsi_overbought"])
        return exits.fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short entry signals: N-day return above |threshold| AND RSI overbought.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short entry signals.
        """
        ohlcv = ohlcv.copy()
        returns = self._compute_returns(ohlcv)
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        threshold = abs(self.params["return_threshold"])
        short_entries = (returns > threshold) & (rsi > self.params["rsi_overbought"])
        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short exit signals: N-day return turns negative OR RSI oversold.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short exit signals.
        """
        ohlcv = ohlcv.copy()
        returns = self._compute_returns(ohlcv)
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        short_exits = (returns < 0) | (rsi < self.params["rsi_oversold"])
        return short_exits.fillna(False)
