"""Strategy D: Mean Reversion Bollinger + RSI (VectorBT).

Entry Long:  Close <= BB Lower AND RSI < oversold AND volatility filter pass
Entry Short: Close >= BB Upper AND RSI > overbought AND volatility filter pass
Exit Long:   Close >= BB Middle (take profit)
Exit Short:  Close <= BB Middle (take profit)
Stop Loss:   Handled via VBT sl_stop parameter in run_backtest override.

Reference: src/bot/strategy/mean_reversion_bb.py (event-driven version)
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import vectorbt as vbt

from backtest_tool.strategies.base_vbt import FREQ_MAP, BaseVBTStrategy
from backtest_tool.strategies.indicators.atr import compute_atr_percentile
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_rsi


class MeanReversionBBVBT(BaseVBTStrategy):
    """Bollinger Band + RSI mean reversion strategy for VectorBT."""

    name = "mean_reversion_bb"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "stop_loss_pct": 1.5,
        "atr_percentile": 50,
        "atr_lookback": 100,
        "leverage": 1,
        "use_atr_stop": False,
        "atr_stop_mult": 2.0,
    }
    required_timeframe = "1h"

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals: Close <= BB Lower AND RSI < oversold AND vol filter.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long entry signals.
        """
        ohlcv = ohlcv.copy()
        bb_upper, bb_middle, bb_lower = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        vol_filter = compute_atr_percentile(
            ohlcv["high"],
            ohlcv["low"],
            ohlcv["close"],
            atr_period=14,
            lookback=self.params["atr_lookback"],
            percentile=self.params["atr_percentile"],
        )

        entries = (
            (ohlcv["close"] <= bb_lower)
            & (rsi < self.params["rsi_oversold"])
            & vol_filter
        )
        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals: Close >= BB Middle (take profit at mean).

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of long exit signals.
        """
        ohlcv = ohlcv.copy()
        _, bb_middle, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )

        exits = ohlcv["close"] >= bb_middle
        return exits.fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short entry signals: Close >= BB Upper AND RSI > overbought AND vol filter.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short entry signals.
        """
        ohlcv = ohlcv.copy()
        bb_upper, _, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])

        vol_filter = compute_atr_percentile(
            ohlcv["high"],
            ohlcv["low"],
            ohlcv["close"],
            atr_period=14,
            lookback=self.params["atr_lookback"],
            percentile=self.params["atr_percentile"],
        )

        short_entries = (
            (ohlcv["close"] >= bb_upper)
            & (rsi > self.params["rsi_overbought"])
            & vol_filter
        )
        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short exit signals: Close <= BB Middle (take profit at mean).

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.

        Returns:
            Boolean Series of short exit signals.
        """
        ohlcv = ohlcv.copy()
        _, bb_middle, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )

        short_exits = ohlcv["close"] <= bb_middle
        return short_exits.fillna(False)

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
    ) -> vbt.Portfolio:
        """Execute backtest with stop-loss via VBT sl_stop parameter.

        Overrides base to add sl_stop for the fixed stop-loss mechanism.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.
            initial_capital: Starting capital.
            fees: Trading fee rate.
            slippage: Slippage rate.
            leverage: Leverage multiplier.

        Returns:
            VBT Portfolio object.
        """
        ohlcv = ohlcv.copy()
        close = ohlcv["close"]

        entries = self.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = self.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = self.generate_short_entries(ohlcv).fillna(False).astype(bool)
        short_exits = self.generate_short_exits(ohlcv).fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)
        total_fees = fees + slippage

        # Determine stop loss
        if self.params.get("use_atr_stop", False):
            from backtest_tool.strategies.indicators.atr import compute_atr
            atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
            avg_atr_ratio = float((atr / ohlcv["close"]).mean())
            atr_mult = self.params.get("atr_stop_mult", 2.0)
            sl_stop = avg_atr_ratio * atr_mult
        else:
            sl_stop = self.params["stop_loss_pct"] / 100.0

        portfolio = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            init_cash=initial_capital,
            fees=total_fees,
            freq=freq,
            direction="both",
            sl_stop=sl_stop,
        )

        return portfolio
