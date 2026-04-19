"""短線策略家族 — 1h 時框，精選信號，EMA200 方向過濾避免逆勢。

核心修正：
  - 全部策略加入 EMA200 方向偏向：大趨勢多頭只做多、大趨勢空頭只做空
  - QuickBBReversion1H: ADX < 20 (橫盤) + EMA200 方向 + RSI 嚴格
  - MACDScalper1H: MACD 零線 + EMA200 方向 + ADX 強度
  - SupertrendScalper1H: period=20/mult=4.0 (減少假翻轉) + EMA200
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.macd import compute_macd
from backtest_tool.strategies.indicators.supertrend import compute_supertrend


# ---------------------------------------------------------------------------
# QuickBBReversion1H — BB 均值回歸 + ADX 橫盤 + EMA200 方向
# ---------------------------------------------------------------------------
class QuickBBReversion1H(BaseVBTStrategy):
    """1h BB 均值回歸。

    進場條件（三重過濾）：
      1. ADX < adx_max  → 確認橫盤，避免趨勢市打臉
      2. close > EMA200 做多 / close < EMA200 做空（大方向過濾）
      3. BB 觸軌 + RSI 超賣/超買

    出場：BB 中軌（獲利）或 BB軌±ATR（停損）
    目標頻率：20-50 次/年（BTC 1h）
    """

    name = "quick_bb_reversion_1h"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period":      20,
        "bb_std":         2.0,
        "rsi_period":     14,
        "rsi_oversold":   33,
        "rsi_overbought": 67,
        "adx_period":     14,
        "adx_max":        22,
        "ema_period":     200,
        "atr_period":     14,
        "atr_mult":       1.5,
        "leverage":       1,
    }

    def _stop_long(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        return (lo - self.params["atr_mult"] * atr).shift(1)

    def _stop_short(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        return (up + self.params["atr_mult"] * atr).shift(1)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] <= lo) &
            (rsi < self.params["rsi_oversold"]) &
            (adx < self.params["adx_max"]) &
            (ohlcv["close"] > ema)          # 只在大趨勢多頭做多
        ).fillna(False)

    def extra_vbt_kwargs(self, ohlcv: pd.DataFrame) -> dict:
        """VBT 內建固定百分比停損：確保虧損嚴格封頂在 sl_stop 內。"""
        return {
            "open": ohlcv["open"],
            "high": ohlcv["high"],
            "low": ohlcv["low"],
            "sl_stop": self.params["atr_mult"] * 0.01,  # e.g. 1.5 * 1% = 1.5% SL
        }

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] >= up) &
            (rsi > self.params["rsi_overbought"]) &
            (adx < self.params["adx_max"]) &
            (ohlcv["close"] < ema)          # 只在大趨勢空頭做空
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# MACDScalper1H — MACD 零線確認 + EMA200 + ADX 趨勢過濾
# ---------------------------------------------------------------------------
class MACDScalper1H(BaseVBTStrategy):
    """1h MACD 短線跟隨。

    進場條件：
      - MACD 金叉（hist 從負轉正）
      - MACD 線 > 0（上穿零線，確認動能）
      - close > EMA200（大趨勢多頭）
      - ADX > adx_min（需要趨勢強度）

    出場：MACD 死叉 或 ATR 停損
    目標頻率：30-80 次/年（BTC 1h）
    """

    name = "macd_scalper_1h"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "macd_fast":   12,
        "macd_slow":   26,
        "macd_signal": 9,
        "ema_period":  200,
        "adx_min":     20,
        "atr_period":  14,
        "atr_mult":    1.5,
        "leverage":    1,
    }

    def _macd_data(self, ohlcv):
        return compute_macd(
            ohlcv["close"], self.params["macd_fast"],
            self.params["macd_slow"], self.params["macd_signal"],
        )

    def extra_vbt_kwargs(self, ohlcv: pd.DataFrame) -> dict:
        return {
            "open": ohlcv["open"], "high": ohlcv["high"], "low": ohlcv["low"],
            "sl_stop": self.params["atr_mult"] * 0.01,
        }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, _, hist = self._macd_data(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        cross_up = (hist > 0) & (hist.shift(1) <= 0)
        return (
            cross_up & (macd > 0) &
            (ohlcv["close"] > ema) &
            (adx > self.params["adx_min"])
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, hist = self._macd_data(ohlcv)
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        stop = (ohlcv["low"].rolling(10).min() - self.params["atr_mult"] * atr).shift(1)
        cross_dn = (hist < 0) & (hist.shift(1) >= 0)
        return (cross_dn | (ohlcv["close"] < stop)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, _, hist = self._macd_data(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        cross_dn = (hist < 0) & (hist.shift(1) >= 0)
        return (
            cross_dn & (macd < 0) &
            (ohlcv["close"] < ema) &
            (adx > self.params["adx_min"])
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, hist = self._macd_data(ohlcv)
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        stop = (ohlcv["high"].rolling(10).max() + self.params["atr_mult"] * atr).shift(1)
        cross_up = (hist > 0) & (hist.shift(1) <= 0)
        return (cross_up | (ohlcv["close"] > stop)).fillna(False)


# ---------------------------------------------------------------------------
# SupertrendScalper1H — 長週期 Supertrend 翻轉 + EMA200 方向
# ---------------------------------------------------------------------------
class SupertrendScalper1H(BaseVBTStrategy):
    """1h Supertrend 翻轉進場。

    使用較長週期（period=20, mult=4.0）減少假翻轉；
    EMA200 方向過濾：多頭市場只做多翻轉，空頭市場只做空翻轉。
    目標頻率：25-60 次/年（BTC 1h）
    """

    name = "supertrend_scalper_1h"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "st_period":   20,   # 較長，減少假翻轉（原10）
        "st_mult":     4.0,  # 較寬，減少假翻轉（原3.0）
        "ema_period":  200,
        "leverage":    1,
    }

    def _direction(self, ohlcv):
        _, direction = compute_supertrend(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["st_period"], self.params["st_mult"],
        )
        return direction

    def extra_vbt_kwargs(self, ohlcv: pd.DataFrame) -> dict:
        return {
            "open": ohlcv["open"], "high": ohlcv["high"], "low": ohlcv["low"],
            "sl_stop": 0.02,  # Supertrend 趨勢策略稍寬 2%
        }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir_ = self._direction(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        flip_bull = (dir_ == 1) & (dir_.shift(1) == -1)
        return (flip_bull & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir_ = self._direction(ohlcv)
        return ((dir_ == -1) & (dir_.shift(1) == 1)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir_ = self._direction(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        flip_bear = (dir_ == -1) & (dir_.shift(1) == 1)
        return (flip_bear & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir_ = self._direction(ohlcv)
        return ((dir_ == 1) & (dir_.shift(1) == -1)).fillna(False)
