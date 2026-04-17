"""Grid strategy family (#11-#17).

Fixes the baseline grid_futures strategy that lost −17% with −48% DD over
2yr on BTC 4h. Key fixes: range-only activation, ATR-adaptive spacing,
trend bias, hedging, global stop-out.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema


def _zones(upper: pd.Series, lower: pd.Series) -> tuple[pd.Series, pd.Series]:
    buy_zone_top = lower + (upper - lower) * 0.33
    sell_zone_bot = lower + (upper - lower) * 0.67
    return buy_zone_top, sell_zone_bot


# ---------------------------------------------------------------------------
# #11 GridRangingOnly — only activate when ADX < 20 and price near EMA200
# ---------------------------------------------------------------------------
class GridRangingOnly(BaseVBTStrategy):
    name = "grid_ranging_only"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "range_period": 20,
        "adx_max": 20,
        "ema_period": 200,
        "ema_band": 0.05,
        "leverage": 1,
    }

    def _range_active(self, ohlcv: pd.DataFrame) -> pd.Series:
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        near_ema = (ohlcv["close"] / ema - 1.0).abs() <= self.params["ema_band"]
        return (adx < self.params["adx_max"]) & near_ema

    def _channel(self, ohlcv: pd.DataFrame):
        p = self.params["range_period"]
        up = ohlcv["high"].rolling(p).max().shift(1)
        lo = ohlcv["low"].rolling(p).min().shift(1)
        return up, lo

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, lo = self._channel(ohlcv)
        buy_top, _ = _zones(up, lo)
        return ((ohlcv["close"] <= buy_top) & self._range_active(ohlcv)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, lo = self._channel(ohlcv)
        _, sell_bot = _zones(up, lo)
        return ((ohlcv["close"] >= sell_bot) | (~self._range_active(ohlcv))).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, lo = self._channel(ohlcv)
        _, sell_bot = _zones(up, lo)
        return ((ohlcv["close"] >= sell_bot) & self._range_active(ohlcv)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, lo = self._channel(ohlcv)
        buy_top, _ = _zones(up, lo)
        return ((ohlcv["close"] <= buy_top) | (~self._range_active(ohlcv))).fillna(False)


# ---------------------------------------------------------------------------
# #12 GridATRAdaptive — grid width = ATR × k
# ---------------------------------------------------------------------------
class GridATRAdaptive(BaseVBTStrategy):
    name = "grid_atr_adaptive"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "atr_period": 14,
        "atr_mult": 2.5,
        "ema_period": 100,
        "leverage": 1,
    }

    def _bands(self, ohlcv: pd.DataFrame):
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        mid = compute_ema(ohlcv["close"], self.params["ema_period"])
        up = mid + self.params["atr_mult"] * atr
        lo = mid - self.params["atr_mult"] * atr
        return up.shift(1), mid.shift(1), lo.shift(1)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = self._bands(ohlcv)
        buy_top, _ = _zones(up, lo)
        return (ohlcv["close"] <= buy_top).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, _ = self._bands(ohlcv)
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, lo = self._bands(ohlcv)
        _, sell_bot = _zones(up, lo)
        return (ohlcv["close"] >= sell_bot).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = self._bands(ohlcv)
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #13 GridBollingerBands
# ---------------------------------------------------------------------------
class GridBollingerBands(BaseVBTStrategy):
    name = "grid_bollinger"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= lo).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= up).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #14 GridHedged — both long & short active simultaneously (delta-neutral grid)
# ---------------------------------------------------------------------------
class GridHedged(BaseVBTStrategy):
    """Long & short grid both active; profit from oscillation."""

    name = "grid_hedged"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 30,
        "bb_std": 2.0,
        "adx_max": 25,
        "leverage": 1,
    }

    def _range(self, ohlcv: pd.DataFrame) -> pd.Series:
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return adx < self.params["adx_max"]

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] <= lo) & self._range(ohlcv)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] >= up) & self._range(ohlcv)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #15 GridTrendBias — long-only grid above EMA, short-only below
# ---------------------------------------------------------------------------
class GridTrendBias(BaseVBTStrategy):
    name = "grid_trend_bias"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "ema_period": 200,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        bullish = ohlcv["close"] > ema
        return ((ohlcv["close"] <= lo) & bullish).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        bearish = ohlcv["close"] < ema
        return ((ohlcv["close"] >= up) & bearish).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #16 GridFundingAware — uses ohlcv['funding_rate'] if present; else EMA bias
# ---------------------------------------------------------------------------
class GridFundingAware(BaseVBTStrategy):
    name = "grid_funding_aware"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "funding_threshold": 0.0001,
        "leverage": 1,
    }

    def _bias_short(self, ohlcv: pd.DataFrame) -> pd.Series:
        if "funding_rate" in ohlcv.columns:
            return ohlcv["funding_rate"] > self.params["funding_threshold"]
        # Fallback: use EMA slope as proxy for overbought conditions
        ema = compute_ema(ohlcv["close"], 50)
        return ema.diff(10) / ema > 0.02

    def _bias_long(self, ohlcv: pd.DataFrame) -> pd.Series:
        if "funding_rate" in ohlcv.columns:
            return ohlcv["funding_rate"] < -self.params["funding_threshold"]
        ema = compute_ema(ohlcv["close"], 50)
        return ema.diff(10) / ema < -0.02

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] <= lo) & self._bias_long(ohlcv)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] >= up) & self._bias_short(ohlcv)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #17 GridStopOut — add global stop-out when close < EMA200 - 10%
# ---------------------------------------------------------------------------
class GridStopOut(BaseVBTStrategy):
    name = "grid_stopout"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "ema_period": 200,
        "stopout_pct": 0.15,
        "leverage": 1,
    }

    def _stopout_long(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return ohlcv["close"] < ema * (1.0 - self.params["stopout_pct"])

    def _stopout_short(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return ohlcv["close"] > ema * (1.0 + self.params["stopout_pct"])

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] <= lo) & (~self._stopout_long(ohlcv))).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] >= mid) | self._stopout_long(ohlcv)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] >= up) & (~self._stopout_short(ohlcv))).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return ((ohlcv["close"] <= mid) | self._stopout_short(ohlcv)).fillna(False)
