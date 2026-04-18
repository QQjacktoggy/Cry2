"""Momentum / Breakout family (#26-#34)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.heikin_ashi import compute_heikin_ashi
from backtest_tool.strategies.indicators.ichimoku import compute_ichimoku
from backtest_tool.strategies.indicators.keltner import compute_keltner
from backtest_tool.strategies.indicators.macd import compute_macd
from backtest_tool.strategies.indicators.stoch import compute_roc
from backtest_tool.strategies.indicators.supertrend import compute_supertrend


# ---------------------------------------------------------------------------
# #26 MomentumROC
# ---------------------------------------------------------------------------
class MomentumROC(BaseVBTStrategy):
    name = "momentum_roc"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "roc_period": 20,
        "entry_threshold": 5.0,
        "exit_threshold": 0.0,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc > self.params["entry_threshold"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc < self.params["exit_threshold"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc < -self.params["entry_threshold"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc > -self.params["exit_threshold"]).fillna(False)


# ---------------------------------------------------------------------------
# #27 MomentumRanking — uses symbol-local high-ROC percentile (proxy for ranking)
# ---------------------------------------------------------------------------
class MomentumRanking(BaseVBTStrategy):
    """In single-symbol mode we use rolling ROC percentile as a proxy for
    'ranking first'. Top-30% momentum → long, bottom-30% → short."""

    name = "momentum_ranking"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "roc_period": 30,
        "lookback": 120,
        "upper_pct": 70,
        "lower_pct": 30,
        "leverage": 1,
    }

    def _percentile(self, roc: pd.Series) -> pd.Series:
        lb = self.params["lookback"]
        return roc.rolling(lb).rank(pct=True) * 100

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        return (pct > self.params["upper_pct"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        return (pct < 50).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        return (pct < self.params["lower_pct"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        return (pct > 50).fillna(False)


# ---------------------------------------------------------------------------
# MomentumRankingV2 — 原版 + ADX 橫盤過濾 + ATR Chandelier 追蹤停損
# 改善：
#   1. ADX < adx_threshold 時不進場（避開震盪盤）
#   2. 出場改用 ATR Chandelier 追蹤停損（讓贏單跑、快速砍虧損單）
#   3. lookback 縮短至 90 天（加快訊號反應）
# ---------------------------------------------------------------------------
class MomentumRankingV2(BaseVBTStrategy):
    """MomentumRanking with ADX trend filter + ATR chandelier trailing stop."""

    name = "momentum_ranking_v2"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "roc_period":     30,
        "lookback":       90,
        "upper_pct":      70,
        "lower_pct":      30,
        "adx_period":     14,
        "adx_threshold":  20,
        "atr_period":     14,
        "atr_mult":       2.0,
        "leverage":       1,
    }

    def _percentile(self, roc: pd.Series) -> pd.Series:
        return roc.rolling(self.params["lookback"]).rank(pct=True) * 100

    def _chandelier_long(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        hh = ohlcv["high"].rolling(self.params["lookback"]).max()
        return (hh - self.params["atr_mult"] * atr).shift(1)

    def _chandelier_short(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        ll = ohlcv["low"].rolling(self.params["lookback"]).min()
        return (ll + self.params["atr_mult"] * atr).shift(1)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (pct > self.params["upper_pct"]) & (adx > self.params["adx_threshold"])
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        stop = self._chandelier_long(ohlcv)
        return (ohlcv["close"] < stop).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        pct = self._percentile(roc)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (pct < self.params["lower_pct"]) & (adx > self.params["adx_threshold"])
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        stop = self._chandelier_short(ohlcv)
        return (ohlcv["close"] > stop).fillna(False)


# ---------------------------------------------------------------------------
# #28 BreakoutSqueeze — TTM squeeze: BB inside KC → squeeze, breakout trade
# ---------------------------------------------------------------------------
class BreakoutSqueeze(BaseVBTStrategy):
    name = "breakout_squeeze"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "kc_ema": 20,
        "kc_atr": 10,
        "kc_mult": 1.5,
        "leverage": 1,
    }

    def _squeeze_released_up(self, ohlcv: pd.DataFrame) -> pd.Series:
        bb_u, _, bb_l = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        kc_u, _, kc_l = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        squeeze = (bb_u < kc_u) & (bb_l > kc_l)
        released = squeeze.shift(1) & ~squeeze
        # Release direction = current close vs 20-bar high
        hh = ohlcv["high"].rolling(self.params["bb_period"]).max().shift(1)
        return released & (ohlcv["close"] > hh)

    def _squeeze_released_down(self, ohlcv: pd.DataFrame) -> pd.Series:
        bb_u, _, bb_l = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        kc_u, _, kc_l = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        squeeze = (bb_u < kc_u) & (bb_l > kc_l)
        released = squeeze.shift(1) & ~squeeze
        ll = ohlcv["low"].rolling(self.params["bb_period"]).min().shift(1)
        return released & (ohlcv["close"] < ll)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        return self._squeeze_released_up(ohlcv).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] < ema).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        return self._squeeze_released_down(ohlcv).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] > ema).fillna(False)


# ---------------------------------------------------------------------------
# #29 OpeningRangeBreakout — UTC daily first-bar breakout
# ---------------------------------------------------------------------------
class OpeningRangeBreakout(BaseVBTStrategy):
    name = "opening_range_breakout"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "range_hours": 4,
        "leverage": 1,
    }

    def _daily_range(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """First-bar OR of each UTC day: propagate forward."""
        idx = ohlcv.index
        hour = idx.hour if hasattr(idx, "hour") else pd.to_datetime(idx).hour
        is_first = pd.Series(hour == 0, index=idx)
        # Build series aligned to each day's first bar H/L
        first_high = ohlcv["high"].where(is_first)
        first_low = ohlcv["low"].where(is_first)
        day_high = first_high.groupby(idx.date).ffill()
        day_low = first_low.groupby(idx.date).ffill()
        return day_high, day_low

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dh, _ = self._daily_range(ohlcv)
        return (ohlcv["close"] > dh).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dh, dl = self._daily_range(ohlcv)
        mid = (dh + dl) / 2.0
        return (ohlcv["close"] < mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, dl = self._daily_range(ohlcv)
        return (ohlcv["close"] < dl).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        dh, dl = self._daily_range(ohlcv)
        mid = (dh + dl) / 2.0
        return (ohlcv["close"] > mid).fillna(False)


# ---------------------------------------------------------------------------
# #30 VolatilityExpansion
# ---------------------------------------------------------------------------
class VolatilityExpansion(BaseVBTStrategy):
    name = "volatility_expansion"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "atr_short": 14,
        "atr_long": 50,
        "atr_mult": 1.5,
        "vol_ma": 20,
        "vol_mult": 1.3,
        "leverage": 1,
    }

    def _conditions(self, ohlcv: pd.DataFrame):
        atr_s = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_short"])
        atr_l = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_long"])
        vol_expand = atr_s > atr_l * self.params["atr_mult"]
        vol_ma = ohlcv["volume"].rolling(self.params["vol_ma"]).mean()
        vol_ok = ohlcv["volume"] > vol_ma * self.params["vol_mult"]
        return vol_expand & vol_ok

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        cond = self._conditions(ohlcv)
        ema = compute_ema(ohlcv["close"], 20)
        return (cond & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] < ema).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        cond = self._conditions(ohlcv)
        ema = compute_ema(ohlcv["close"], 20)
        return (cond & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] > ema).fillna(False)


# ---------------------------------------------------------------------------
# #31 MACDTrendFollow
# ---------------------------------------------------------------------------
class MACDTrendFollow(BaseVBTStrategy):
    name = "macd_trend"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "fast": 12,
        "slow": 26,
        "signal": 9,
        "ema_period": 200,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, sig, hist = compute_macd(
            ohlcv["close"], self.params["fast"], self.params["slow"], self.params["signal"]
        )
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        cross_up = (macd > sig) & (macd.shift(1) <= sig.shift(1))
        return (cross_up & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, sig, _ = compute_macd(
            ohlcv["close"], self.params["fast"], self.params["slow"], self.params["signal"]
        )
        return (macd < sig).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, sig, _ = compute_macd(
            ohlcv["close"], self.params["fast"], self.params["slow"], self.params["signal"]
        )
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        cross_dn = (macd < sig) & (macd.shift(1) >= sig.shift(1))
        return (cross_dn & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        macd, sig, _ = compute_macd(
            ohlcv["close"], self.params["fast"], self.params["slow"], self.params["signal"]
        )
        return (macd > sig).fillna(False)


# ---------------------------------------------------------------------------
# #32 SupertrendFollow
# ---------------------------------------------------------------------------
class SupertrendFollow(BaseVBTStrategy):
    name = "supertrend_follow"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "atr_period": 10,
        "atr_mult": 3.0,
        "leverage": 1,
    }

    def _dir(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, d = compute_supertrend(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["atr_period"], self.params["atr_mult"],
        )
        return d

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        d = self._dir(ohlcv)
        return ((d == 1) & (d.shift(1) == -1)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        d = self._dir(ohlcv)
        return (d == -1).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        d = self._dir(ohlcv)
        return ((d == -1) & (d.shift(1) == 1)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        d = self._dir(ohlcv)
        return (d == 1).fillna(False)


# ---------------------------------------------------------------------------
# #33 IchimokuCloudBreak
# ---------------------------------------------------------------------------
class IchimokuCloudBreak(BaseVBTStrategy):
    name = "ichimoku_cloud"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "tenkan": 9,
        "kijun": 26,
        "senkou_b": 52,
        "disp": 26,
        "leverage": 1,
    }

    def _cloud(self, ohlcv: pd.DataFrame):
        ich = compute_ichimoku(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["tenkan"], self.params["kijun"],
            self.params["senkou_b"], self.params["disp"],
        )
        return ich["senkou_a"], ich["senkou_b"], ich["tenkan"], ich["kijun"]

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        sa, sb, t, k = self._cloud(ohlcv)
        top = pd.concat([sa, sb], axis=1).max(axis=1)
        return ((ohlcv["close"] > top) & (t > k)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        sa, sb, t, k = self._cloud(ohlcv)
        top = pd.concat([sa, sb], axis=1).max(axis=1)
        return (ohlcv["close"] < top).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        sa, sb, t, k = self._cloud(ohlcv)
        bot = pd.concat([sa, sb], axis=1).min(axis=1)
        return ((ohlcv["close"] < bot) & (t < k)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        sa, sb, t, k = self._cloud(ohlcv)
        bot = pd.concat([sa, sb], axis=1).min(axis=1)
        return (ohlcv["close"] > bot).fillna(False)


# ---------------------------------------------------------------------------
# #34 HeikinAshiTrend
# ---------------------------------------------------------------------------
class HeikinAshiTrend(BaseVBTStrategy):
    name = "heikin_ashi_trend"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "streak": 3,
        "ema_period": 100,
        "leverage": 1,
    }

    def _streak(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        ha = compute_heikin_ashi(ohlcv)
        bull = ha["ha_close"] > ha["ha_open"]
        bear = ha["ha_close"] < ha["ha_open"]
        n = self.params["streak"]
        bull_streak = bull.rolling(n).sum() == n
        bear_streak = bear.rolling(n).sum() == n
        return bull_streak, bear_streak

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        bull, _ = self._streak(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (bull & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ha = compute_heikin_ashi(ohlcv)
        return (ha["ha_close"] < ha["ha_open"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, bear = self._streak(ohlcv)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (bear & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ha = compute_heikin_ashi(ohlcv)
        return (ha["ha_close"] > ha["ha_open"]).fillna(False)
