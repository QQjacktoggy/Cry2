"""短線策略家族 — 1h 時框，精選信號，EMA200 方向過濾避免逆勢。

核心修正：
  - 全部策略加入 EMA200 方向偏向：大趨勢多頭只做多、大趨勢空頭只做空
  - QuickBBReversion1H: ADX < 20 (橫盤) + EMA200 方向 + RSI 嚴格
  - MACDScalper1H: MACD 零線 + EMA200 方向 + ADX 強度
  - SupertrendScalper1H: period=20/mult=4.0 (減少假翻轉) + EMA200

升級版（Pro）：
  - MACDScalperPro1H: + 4h MTF 確認 + ATR 百分位過濾 + 追蹤止損
  - SupertrendScalperPro1H: + 4h MTF 確認 + ATR 百分位過濾 + 追蹤止損
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.atr import compute_atr, compute_atr_percentile
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


# ===========================================================================
# 升級版 Pro 策略：三重過濾 + 追蹤止損
# ===========================================================================

# ---------------------------------------------------------------------------
# MACDScalperPro1H — 四重確認：1h MACD + 4h MTF + ATR百分位 + EMA200
# ---------------------------------------------------------------------------
class MACDScalperPro1H(BaseVBTStrategy):
    """1h MACD 精品版。

    三重過濾（解決假信號問題）：
      1. 1h MACD 金叉（hist: 負→正）且 MACD 線 > 0（動能確認）
      2. EMA200 方向過濾（多頭市場只做多）
      3. ATR 百分位 25-80%（健康波動，過濾極端行情）+ 1h ADX > 20

    出場：
      - 1h MACD 死叉（先到先出）
      - VBT 固定止損 sl_stop + 固定獲利 tp_stop（R:R 2.67:1）

    設計重點：
      - 不使用 4h MTF 方向過濾（防止 BTC 牛市回調期被封鎖）
      - ATR 百分位過濾足以處理 SOL 極端波動
      - 固定 R:R 確保期望值為正（WR > 27% 即獲利）

    目標：交易次數 60-120次/年，月勝率 50%+
    """

    name = "macd_scalper_pro_1h"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "macd_fast":    12,
        "macd_slow":    26,
        "macd_signal":  9,
        "ema_period":   200,
        "adx_min":      20,
        "atr_pct_low":  0,     # 0 = 不限下界（BTC/ETH 用，SOL 可設 25）
        "atr_pct_high": 90,    # 過濾極端波動（SOL 建議設 80-85）
        "sl_pct":       0.02,  # 止損 2%
        "tp_pct":       None,  # None = 由 MACD 死叉決定出場（None 表示不設固定 TP）
        "leverage":     1,
    }

    def _atr_ok(self, ohlcv: pd.DataFrame) -> pd.Series:
        """ATR 在健康範圍內。atr_pct_low=0 表示不限下界；atr_pct_high=100 表示不限上界。"""
        result = pd.Series(True, index=ohlcv.index)
        if self.params["atr_pct_low"] > 0:
            result = result & compute_atr_percentile(
                ohlcv["high"], ohlcv["low"], ohlcv["close"],
                14, 100, self.params["atr_pct_low"],
            )
        if self.params["atr_pct_high"] < 100:
            result = result & ~compute_atr_percentile(
                ohlcv["high"], ohlcv["low"], ohlcv["close"],
                14, 100, self.params["atr_pct_high"],
            )
        return result

    def _macd_data(self, ohlcv):
        return compute_macd(
            ohlcv["close"], self.params["macd_fast"],
            self.params["macd_slow"], self.params["macd_signal"],
        )

    def extra_vbt_kwargs(self, ohlcv: pd.DataFrame) -> dict:
        kwargs = {"open": ohlcv["open"], "high": ohlcv["high"], "low": ohlcv["low"],
                  "sl_stop": self.params["sl_pct"]}
        if self.params.get("tp_pct") is not None:
            kwargs["tp_stop"] = self.params["tp_pct"]
        return kwargs

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, _, hist = self._macd_data(ohlcv)
        ema   = compute_ema(ohlcv["close"], self.params["ema_period"])
        adx1h = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ok    = self._atr_ok(ohlcv)
        cross_up = (hist > 0) & (hist.shift(1) <= 0)
        return (
            cross_up &
            (macd > 0) &
            (ohlcv["close"] > ema) &
            (adx1h > self.params["adx_min"]) &
            ok
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, hist = self._macd_data(ohlcv)
        return ((hist < 0) & (hist.shift(1) >= 0)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, _, hist = self._macd_data(ohlcv)
        ema   = compute_ema(ohlcv["close"], self.params["ema_period"])
        adx1h = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ok    = self._atr_ok(ohlcv)
        cross_dn = (hist < 0) & (hist.shift(1) >= 0)
        return (
            cross_dn &
            (macd < 0) &
            (ohlcv["close"] < ema) &
            (adx1h > self.params["adx_min"]) &
            ok
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, hist = self._macd_data(ohlcv)
        return ((hist > 0) & (hist.shift(1) <= 0)).fillna(False)


# ---------------------------------------------------------------------------
# SupertrendScalperPro1H — 4h MTF 同向確認 + ATR 過濾 + 追蹤止損
# ---------------------------------------------------------------------------
class SupertrendScalperPro1H(BaseVBTStrategy):
    """1h Supertrend 精品版。

    解決假翻轉問題：
      1. 1h Supertrend 方向翻轉（-1→1 多頭翻轉）
      2. 4h ADX > 20（確認 4h 在趨勢中，不限方向）
      3. ATR 百分位 25-80%（過濾極端行情）
      4. EMA200 方向確認

    設計重點：
      - 放棄「4h ST 同向」（等待 4h 翻轉 = 進場過晚，最好行情已過）
      - 改用「4h ADX > 20」= 市場在趨勢中，不限制方向
      - 固定 R:R：sl_stop=2.5%, tp_stop=5%（R:R = 2:1）
      - WR > 33% 即正期望值

    出場：
      - 1h Supertrend 反向翻轉（先到先出）
      - VBT 固定止損 + 固定獲利

    目標：交易次數 30-70次/年，月勝率 50%+
    """

    name = "supertrend_scalper_pro_1h"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "st_period":    20,
        "st_mult":      4.0,
        "ema_period":   200,
        "atr_pct_low":  0,     # 0 = 不限下界（ETH/SOL 用）
        "atr_pct_high": 85,    # 過濾極端波動（SOL 可設 80-85）
        "sl_pct":       0.025,
        "tp_pct":       0.05,  # R:R = 2:1（WR > 33% 即正期望值）
        "leverage":     1,
    }

    def _direction_1h(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, d = compute_supertrend(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["st_period"], self.params["st_mult"],
        )
        return d

    def _htf_adx(self, ohlcv: pd.DataFrame) -> pd.Series:
        """4h ADX — 要求趨勢性（不要求方向）。"""
        df4h = ohlcv[["high", "low", "close"]].resample("4h").agg(
            {"high": "max", "low": "min", "close": "last"}
        ).dropna()
        adx4h = compute_adx(df4h["high"], df4h["low"], df4h["close"], 14)
        return adx4h.reindex(ohlcv.index, method="ffill").fillna(0)

    def _atr_ok(self, ohlcv: pd.DataFrame) -> pd.Series:
        result = pd.Series(True, index=ohlcv.index)
        if self.params["atr_pct_low"] > 0:
            result = result & compute_atr_percentile(
                ohlcv["high"], ohlcv["low"], ohlcv["close"],
                14, 100, self.params["atr_pct_low"],
            )
        if self.params["atr_pct_high"] < 100:
            result = result & ~compute_atr_percentile(
                ohlcv["high"], ohlcv["low"], ohlcv["close"],
                14, 100, self.params["atr_pct_high"],
            )
        return result

    def extra_vbt_kwargs(self, ohlcv: pd.DataFrame) -> dict:
        return {
            "open": ohlcv["open"], "high": ohlcv["high"], "low": ohlcv["low"],
            "sl_stop": self.params["sl_pct"],
            "tp_stop": self.params["tp_pct"],
        }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir1h = self._direction_1h(ohlcv)
        adx4h = self._htf_adx(ohlcv)
        ema   = compute_ema(ohlcv["close"], self.params["ema_period"])
        ok    = self._atr_ok(ohlcv)
        flip_bull = (dir1h == 1) & (dir1h.shift(1) == -1)
        return (
            flip_bull &
            (adx4h > 20) &              # 4h 需有趨勢（不限方向）
            (ohlcv["close"] > ema) &
            ok
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir1h = self._direction_1h(ohlcv)
        return ((dir1h == -1) & (dir1h.shift(1) == 1)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir1h = self._direction_1h(ohlcv)
        adx4h = self._htf_adx(ohlcv)
        ema   = compute_ema(ohlcv["close"], self.params["ema_period"])
        ok    = self._atr_ok(ohlcv)
        flip_bear = (dir1h == -1) & (dir1h.shift(1) == 1)
        return (
            flip_bear &
            (adx4h > 20) &
            (ohlcv["close"] < ema) &
            ok
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dir1h = self._direction_1h(ohlcv)
        return ((dir1h == 1) & (dir1h.shift(1) == -1)).fillna(False)
