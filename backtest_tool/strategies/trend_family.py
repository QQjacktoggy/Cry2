"""Trend-following strategy family (#1-#10): Donchian variants.

Each strategy targets a specific weakness in the baseline TrendDonchianVBT,
which achieved +48% over 2yr on BTC 4h but had only 16 trades and was
BTC-only. These variants add filters, better exits, and adaptive params
to generalize and improve profitability.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr, compute_atr_percentile
from backtest_tool.strategies.indicators.bollinger import compute_ema
from backtest_tool.strategies.indicators.donchian import compute_donchian
from backtest_tool.strategies.indicators.keltner import compute_keltner


# ---------------------------------------------------------------------------
# #1 TrendDonchianV2 — EMA200 slope filter
# ---------------------------------------------------------------------------
class TrendDonchianV2(BaseVBTStrategy):
    name = "trend_donchian_v2"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "adx_period": 14,
        "adx_threshold": 25,
        "ema_period": 200,
        "leverage": 1,
    }

    def _trend_up(self, close: pd.Series) -> pd.Series:
        ema = compute_ema(close, self.params["ema_period"])
        return (close > ema) & (ema.diff(5) > 0)

    def _trend_down(self, close: pd.Series) -> pd.Series:
        ema = compute_ema(close, self.params["ema_period"])
        return (close < ema) & (ema.diff(5) < 0)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (ohlcv["close"] > up)
            & (adx > self.params["adx_threshold"])
            & self._trend_up(ohlcv["close"])
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (ohlcv["close"] < lo)
            & (adx > self.params["adx_threshold"])
            & self._trend_down(ohlcv["close"])
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #2 TrendDonchianMTF — 1D confirmation (resample internally)
# ---------------------------------------------------------------------------
class TrendDonchianMTF(BaseVBTStrategy):
    name = "trend_donchian_mtf"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "htf_period": 20,
        "adx_threshold": 25,
        "leverage": 1,
    }

    def _htf_bias(self, ohlcv: pd.DataFrame) -> pd.Series:
        daily = ohlcv[["high", "low", "close"]].resample("1D").agg(
            {"high": "max", "low": "min", "close": "last"}
        )
        up, lo, _ = compute_donchian(daily["high"], daily["low"], self.params["htf_period"])
        bias = pd.Series(0, index=daily.index, dtype=int)
        bias[daily["close"] > up] = 1
        bias[daily["close"] < lo] = -1
        bias = bias.shift(1)
        return bias.reindex(ohlcv.index, method="ffill").fillna(0)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        bias = self._htf_bias(ohlcv)
        return (
            (ohlcv["close"] > up) & (adx > self.params["adx_threshold"]) & (bias > 0)
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        bias = self._htf_bias(ohlcv)
        return (
            (ohlcv["close"] < lo) & (adx > self.params["adx_threshold"]) & (bias < 0)
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #3 TrendDonchianATRTrail — ATR trailing stop
# ---------------------------------------------------------------------------
class TrendDonchianATRTrail(BaseVBTStrategy):
    name = "trend_donchian_atr_trail"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "atr_period": 14,
        "atr_mult": 3.0,
        "adx_threshold": 25,
        "leverage": 1,
    }

    def _trail(self, high: pd.Series, low: pd.Series, close: pd.Series, long: bool) -> pd.Series:
        atr = compute_atr(high, low, close, self.params["atr_period"])
        k = self.params["atr_mult"]
        if long:
            return (high.rolling(self.params["entry_period"]).max() - k * atr).shift(1)
        return (low.rolling(self.params["entry_period"]).min() + k * atr).shift(1)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] > up) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        trail = self._trail(ohlcv["high"], ohlcv["low"], ohlcv["close"], long=True)
        return (ohlcv["close"] < trail).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] < lo) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        trail = self._trail(ohlcv["high"], ohlcv["low"], ohlcv["close"], long=False)
        return (ohlcv["close"] > trail).fillna(False)


# ---------------------------------------------------------------------------
# #4 TrendDonchianChandelier
# ---------------------------------------------------------------------------
class TrendDonchianChandelier(BaseVBTStrategy):
    name = "trend_donchian_chandelier"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "chandelier_period": 22,
        "atr_mult": 3.0,
        "adx_threshold": 25,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] > up) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        hh = ohlcv["high"].rolling(self.params["chandelier_period"]).max()
        chand = (hh - self.params["atr_mult"] * atr).shift(1)
        return (ohlcv["close"] < chand).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] < lo) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ll = ohlcv["low"].rolling(self.params["chandelier_period"]).min()
        chand = (ll + self.params["atr_mult"] * atr).shift(1)
        return (ohlcv["close"] > chand).fillna(False)


# ---------------------------------------------------------------------------
# #5 TrendDonchianVolumeConfirm
# ---------------------------------------------------------------------------
class TrendDonchianVolumeConfirm(BaseVBTStrategy):
    name = "trend_donchian_volume"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "adx_threshold": 25,
        "vol_mult": 1.2,
        "vol_ma_period": 20,
        "leverage": 1,
    }

    def _vol_ok(self, vol: pd.Series) -> pd.Series:
        ma = vol.rolling(self.params["vol_ma_period"]).mean()
        return vol > ma * self.params["vol_mult"]

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return (
            (ohlcv["close"] > up)
            & (adx > self.params["adx_threshold"])
            & self._vol_ok(ohlcv["volume"])
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return (
            (ohlcv["close"] < lo)
            & (adx > self.params["adx_threshold"])
            & self._vol_ok(ohlcv["volume"])
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #6 TrendDonchianPyramid — scale in on follow-through
# ---------------------------------------------------------------------------
class TrendDonchianPyramid(BaseVBTStrategy):
    """Pyramid: initial breakout + re-entry on 10-bar highs for follow-through."""

    name = "trend_donchian_pyramid"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "add_period": 10,
        "exit_period": 10,
        "adx_threshold": 25,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up1, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        up2, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["add_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        breakout = (ohlcv["close"] > up1) & (adx > self.params["adx_threshold"])
        addon = (ohlcv["close"] > up2) & (adx > self.params["adx_threshold"])
        return (breakout | addon).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo1, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        _, lo2, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["add_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        breakdown = (ohlcv["close"] < lo1) & (adx > self.params["adx_threshold"])
        addon = (ohlcv["close"] < lo2) & (adx > self.params["adx_threshold"])
        return (breakdown | addon).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #7 TrendDonchianADXSlope
# ---------------------------------------------------------------------------
class TrendDonchianADXSlope(BaseVBTStrategy):
    name = "trend_donchian_adx_slope"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "adx_threshold": 20,
        "adx_slope_bars": 3,
        "leverage": 1,
    }

    def _adx_rising(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        rising = adx.diff(self.params["adx_slope_bars"]) > 0
        return adx, rising

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx, rising = self._adx_rising(ohlcv)
        return (
            (ohlcv["close"] > up) & (adx > self.params["adx_threshold"]) & rising
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx, rising = self._adx_rising(ohlcv)
        return (
            (ohlcv["close"] < lo) & (adx > self.params["adx_threshold"]) & rising
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #8 TrendDonchianTurtle — dual S1/S2 systems
# ---------------------------------------------------------------------------
class TrendDonchianTurtle(BaseVBTStrategy):
    name = "trend_donchian_turtle"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "s1_entry": 20,
        "s1_exit": 10,
        "s2_entry": 55,
        "s2_exit": 20,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up1, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s1_entry"])
        up2, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s2_entry"])
        return ((ohlcv["close"] > up1) | (ohlcv["close"] > up2)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo1, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s1_exit"])
        _, lo2, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s2_exit"])
        return ((ohlcv["close"] < lo1) & (ohlcv["close"] < lo2)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo1, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s1_entry"])
        _, lo2, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s2_entry"])
        return ((ohlcv["close"] < lo1) | (ohlcv["close"] < lo2)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up1, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s1_exit"])
        up2, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["s2_exit"])
        return ((ohlcv["close"] > up1) & (ohlcv["close"] > up2)).fillna(False)


# ---------------------------------------------------------------------------
# #9 TrendDonchianKeltner — Keltner channel breakout
# ---------------------------------------------------------------------------
class TrendDonchianKeltner(BaseVBTStrategy):
    name = "trend_donchian_keltner"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "ema_period": 20,
        "atr_period": 10,
        "atr_mult": 2.0,
        "exit_atr_mult": 1.0,
        "adx_threshold": 20,
        "leverage": 1,
    }

    def _channels(self, ohlcv: pd.DataFrame):
        up, mid, lo = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["ema_period"], self.params["atr_period"], self.params["atr_mult"],
        )
        ex_up, _, ex_lo = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["ema_period"], self.params["atr_period"], self.params["exit_atr_mult"],
        )
        return up.shift(1), lo.shift(1), ex_up.shift(1), ex_lo.shift(1), mid.shift(1)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _, _, _ = self._channels(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] > up) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, _, _, mid = self._channels(ohlcv)
        return (ohlcv["close"] < mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _, _, _ = self._channels(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] < lo) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, _, _, mid = self._channels(ohlcv)
        return (ohlcv["close"] > mid).fillna(False)


# ---------------------------------------------------------------------------
# #10 TrendDonchianAdaptive — ATR-percentile adaptive entry_period
# ---------------------------------------------------------------------------
class TrendDonchianAdaptive(BaseVBTStrategy):
    name = "trend_donchian_adaptive"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "base_period": 20,
        "exit_period": 10,
        "adx_threshold": 25,
        "atr_lookback": 100,
        "leverage": 1,
    }

    def _entry_channel(self, ohlcv: pd.DataFrame):
        high_vol = compute_atr_percentile(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            atr_period=14, lookback=self.params["atr_lookback"], percentile=70,
        )
        low_vol = ~compute_atr_percentile(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            atr_period=14, lookback=self.params["atr_lookback"], percentile=30,
        )
        base = self.params["base_period"]
        period = pd.Series(base, index=ohlcv.index, dtype=int)
        period[high_vol] = max(5, base - 10)
        period[low_vol] = base + 15

        # Manual rolling max/min with variable window — use 2 pre-computed channels & pick
        up_short = ohlcv["high"].rolling(max(5, base - 10)).max().shift(1)
        lo_short = ohlcv["low"].rolling(max(5, base - 10)).min().shift(1)
        up_base = ohlcv["high"].rolling(base).max().shift(1)
        lo_base = ohlcv["low"].rolling(base).min().shift(1)
        up_long = ohlcv["high"].rolling(base + 15).max().shift(1)
        lo_long = ohlcv["low"].rolling(base + 15).min().shift(1)

        up = up_base.copy()
        up[high_vol] = up_short[high_vol]
        up[low_vol] = up_long[low_vol]
        lo = lo_base.copy()
        lo[high_vol] = lo_short[high_vol]
        lo[low_vol] = lo_long[low_vol]
        return up, lo

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _ = self._entry_channel(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] > up) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo = self._entry_channel(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] < lo) & (adx > self.params["adx_threshold"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)
