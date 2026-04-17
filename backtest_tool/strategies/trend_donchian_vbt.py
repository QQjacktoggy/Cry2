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
import vectorbt as vbt

from backtest_tool.strategies.base_vbt import FREQ_MAP, BaseVBTStrategy
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
        "sl_stop_pct": 0,
        "max_hold_bars": 0,
        "leverage": 2,
        "volume_confirm": False,
        "ema_slope_filter": False,
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

        # Optional: volume breakout confirmation (volume > 1.5× 20-bar avg)
        if self.params.get("volume_confirm") and "volume" in ohlcv.columns:
            from backtest_tool.strategies.indicators.volume import compute_volume_ma_ratio
            vol_ratio = compute_volume_ma_ratio(ohlcv["volume"], window=20)
            entries = entries & (vol_ratio > 1.5)

        # Optional: EMA slope filter (EMA20 slope direction)
        if self.params.get("ema_slope_filter"):
            from backtest_tool.strategies.indicators.bollinger import compute_ema
            ema20 = compute_ema(ohlcv["close"], 20)
            ema_slope_up = ema20 > ema20.shift(3)
            entries = entries & ema_slope_up

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

        # Optional: volume breakout confirmation (volume spike confirms breakdown)
        if self.params.get("volume_confirm") and "volume" in ohlcv.columns:
            from backtest_tool.strategies.indicators.volume import compute_volume_ma_ratio
            vol_ratio = compute_volume_ma_ratio(ohlcv["volume"], window=20)
            short_entries = short_entries & (vol_ratio > 1.5)

        # Optional: EMA slope filter (EMA20 slope direction — inverted for shorts)
        if self.params.get("ema_slope_filter"):
            from backtest_tool.strategies.indicators.bollinger import compute_ema
            ema20 = compute_ema(ohlcv["close"], 20)
            ema_slope_down = ema20 < ema20.shift(3)
            short_entries = short_entries & ema_slope_down

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

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
    ) -> vbt.Portfolio:
        """Execute backtest with optional fixed stop-loss.

        Overrides base to add sl_stop when sl_stop_pct > 0.

        Args:
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.
            initial_capital: Starting capital.
            fees: Trading fee rate.
            slippage: Slippage rate.
            leverage: Leverage multiplier.

        Returns:
            VBT Portfolio object.
        """
        sl_stop_pct = self.params.get("sl_stop_pct", 0)
        if sl_stop_pct <= 0:
            return super().run_backtest(ohlcv, initial_capital, fees, slippage, leverage)

        ohlcv = ohlcv.copy()
        close = ohlcv["close"]

        entries = self.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = self.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = self.generate_short_entries(ohlcv).fillna(False).astype(bool)
        short_exits = self.generate_short_exits(ohlcv).fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)
        total_fees = fees + slippage
        sl_stop = sl_stop_pct / 100.0

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
