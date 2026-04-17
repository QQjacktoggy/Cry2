"""Regime / ensemble / risk overlay family (#40-#45).

所有 overlay 策略在單一 DataFrame 內部執行——單 symbol 回測時以「組合訊號」
達成多策略切換/表決；多策略資金配權（#42-#45）為單機簡化版：在同一
symbol 上以『條件權重』調整 size，用動態 position sizing 實作。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr, compute_atr_percentile
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.donchian import compute_donchian
from backtest_tool.strategies.indicators.macd import compute_macd
from backtest_tool.strategies.indicators.supertrend import compute_supertrend


# ---------------------------------------------------------------------------
# #40 RegimeSwitcher — 3-regime classifier → trend/range/chaos
# ---------------------------------------------------------------------------
class RegimeSwitcher(BaseVBTStrategy):
    """Regime classifier:
    - Trend (ADX > 25)    → Donchian breakout（類似 #1 TrendDonchianV2）
    - Range (ADX < 20)    → BB mean reversion（類似 #18 MRBBRegimeFiltered）
    - Chaos (ATR>p80)     → 空手
    """

    name = "regime_switcher"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "bb_period": 20,
        "bb_std": 2.0,
        "adx_trend": 25,
        "adx_range": 20,
        "atr_lookback": 100,
        "chaos_pct": 80,
        "leverage": 1,
    }

    def _regimes(self, ohlcv: pd.DataFrame):
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        chaos = compute_atr_percentile(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            atr_period=14, lookback=self.params["atr_lookback"],
            percentile=self.params["chaos_pct"],
        )
        trend = (adx > self.params["adx_trend"]) & (~chaos)
        rng = (adx < self.params["adx_range"]) & (~chaos)
        return trend, rng

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        trend, rng = self._regimes(ohlcv)
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        _, _, bb_lo = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        trend_long = (ohlcv["close"] > up) & trend
        range_long = (ohlcv["close"] <= bb_lo) & rng
        return (trend_long | range_long).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        trend, rng = self._regimes(ohlcv)
        _, dn, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        _, bb_mid, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        return (
            (trend & (ohlcv["close"] < dn))
            | (rng & (ohlcv["close"] >= bb_mid))
            | (~(trend | rng))
        ).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        trend, rng = self._regimes(ohlcv)
        _, dn, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        bb_up, _, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        trend_short = (ohlcv["close"] < dn) & trend
        range_short = (ohlcv["close"] >= bb_up) & rng
        return (trend_short | range_short).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        trend, rng = self._regimes(ohlcv)
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        _, bb_mid, _ = compute_bollinger(
            ohlcv["close"], self.params["bb_period"], self.params["bb_std"]
        )
        return (
            (trend & (ohlcv["close"] > up))
            | (rng & (ohlcv["close"] <= bb_mid))
            | (~(trend | rng))
        ).fillna(False)


# ---------------------------------------------------------------------------
# #41 EnsembleVote — Donchian / MACD / Supertrend 多數決
# ---------------------------------------------------------------------------
class EnsembleVote(BaseVBTStrategy):
    name = "ensemble_vote"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_period": 20,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "st_atr": 10,
        "st_mult": 3.0,
        "min_votes": 2,
        "leverage": 1,
    }

    def _votes(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        up, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_period"])
        donc_long = (ohlcv["close"] > up).astype(int)
        donc_short = (ohlcv["close"] < lo).astype(int)

        macd, sig, _ = compute_macd(
            ohlcv["close"], self.params["macd_fast"],
            self.params["macd_slow"], self.params["macd_signal"]
        )
        macd_long = (macd > sig).astype(int)
        macd_short = (macd < sig).astype(int)

        _, d = compute_supertrend(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["st_atr"], self.params["st_mult"]
        )
        st_long = (d == 1).astype(int)
        st_short = (d == -1).astype(int)

        long_votes = donc_long + macd_long + st_long
        short_votes = donc_short + macd_short + st_short
        return long_votes, short_votes

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        long_v, _ = self._votes(ohlcv)
        return (long_v >= self.params["min_votes"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        long_v, _ = self._votes(ohlcv)
        return (long_v < self.params["min_votes"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, short_v = self._votes(ohlcv)
        return (short_v >= self.params["min_votes"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, short_v = self._votes(ohlcv)
        return (short_v < self.params["min_votes"]).fillna(False)


# ---------------------------------------------------------------------------
# #42 RiskParityPortfolio — 趨勢系統 + 波動反比位倉（近似）
# 單 symbol 版本：當 ATR 偏低時加倉、ATR 偏高時空手，達到目標「常數月波動」。
# ---------------------------------------------------------------------------
class RiskParityPortfolio(BaseVBTStrategy):
    name = "risk_parity"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_entry": 20,
        "donchian_exit": 10,
        "atr_lookback": 60,
        "vol_cap_pct": 75,
        "leverage": 1,
    }

    def _low_vol(self, ohlcv: pd.DataFrame) -> pd.Series:
        """True when current ATR 低於 lookback 的 pct 分位（可放行進場）。"""
        return ~compute_atr_percentile(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            atr_period=14, lookback=self.params["atr_lookback"],
            percentile=self.params["vol_cap_pct"],
        )

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] > up) & self._low_vol(ohlcv)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] < lo) & self._low_vol(ohlcv)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #43 KellyPositionSizer — 以歷史 win_rate/payoff 動態決定是否進場
# 實作上：只在滾動視窗 Kelly fraction > threshold 時進場。
# ---------------------------------------------------------------------------
class KellyPositionSizer(BaseVBTStrategy):
    name = "kelly_sizer"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_entry": 20,
        "donchian_exit": 10,
        "kelly_lookback": 100,
        "min_kelly": 0.1,
        "leverage": 1,
    }

    def _kelly_ok(self, close: pd.Series) -> pd.Series:
        """Rolling Kelly: 以 close returns 估算 win_rate/payoff。"""
        ret = close.pct_change()
        lb = self.params["kelly_lookback"]
        wins = (ret > 0).rolling(lb).sum()
        losses = (ret < 0).rolling(lb).sum()
        win_rate = wins / (wins + losses).replace(0, np.nan)
        avg_win = ret.where(ret > 0).rolling(lb).mean()
        avg_loss = (-ret.where(ret < 0)).rolling(lb).mean()
        payoff = avg_win / avg_loss.replace(0, np.nan)
        # Kelly = W - (1-W)/payoff
        kelly = win_rate - (1 - win_rate) / payoff.replace(0, np.nan)
        return kelly > self.params["min_kelly"]

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] > up) & self._kelly_ok(ohlcv["close"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] < lo) & self._kelly_ok(ohlcv["close"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #44 DrawdownThrottle — rolling DD > threshold 時暫停進場
# ---------------------------------------------------------------------------
class DrawdownThrottle(BaseVBTStrategy):
    name = "drawdown_throttle"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_entry": 20,
        "donchian_exit": 10,
        "dd_lookback": 30,
        "dd_max": 0.10,
        "leverage": 1,
    }

    def _dd_ok(self, close: pd.Series) -> pd.Series:
        lb = self.params["dd_lookback"]
        roll_max = close.rolling(lb).max()
        dd = 1 - close / roll_max
        return dd < self.params["dd_max"]

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] > up) & self._dd_ok(ohlcv["close"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        return ((ohlcv["close"] < lo) & self._dd_ok(ohlcv["close"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #45 CorrelationPruner — 單 symbol 版：Trend + 慢 RSI 過濾防止同相策略共振
# ---------------------------------------------------------------------------
class CorrelationPruner(BaseVBTStrategy):
    """在多策略場景應過濾相關性，單 symbol 版改為『快慢訊號發散時才進場』，
    防止策略家族共振造成集中風險。"""

    name = "correlation_pruner"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_entry": 20,
        "donchian_exit": 10,
        "rsi_period": 21,
        "rsi_bull_min": 55,
        "rsi_bear_max": 45,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return ((ohlcv["close"] > up) & (rsi > self.params["rsi_bull_min"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_entry"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return ((ohlcv["close"] < lo) & (rsi < self.params["rsi_bear_max"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_exit"])
        return (ohlcv["close"] > up).fillna(False)
