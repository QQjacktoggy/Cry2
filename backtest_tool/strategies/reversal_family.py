"""Reversal family (#35-#39): 非 BB 的反轉系統。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.donchian import compute_donchian
from backtest_tool.strategies.indicators.patterns import detect_pin_bar
from backtest_tool.strategies.indicators.stoch import compute_stochastic


# ---------------------------------------------------------------------------
# #35 RSI2Connors — Connors RSI(2) 短線反轉
# ---------------------------------------------------------------------------
class RSI2Connors(BaseVBTStrategy):
    name = "rsi2_connors"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "rsi_period": 2,
        "oversold": 10,
        "overbought": 90,
        "ema_period": 200,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return ((rsi < self.params["oversold"]) & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return (rsi > 60).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return ((rsi > self.params["overbought"]) & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return (rsi < 40).fillna(False)


# ---------------------------------------------------------------------------
# #36 PinBarReversal — pin bar 於通道邊界
# ---------------------------------------------------------------------------
class PinBarReversal(BaseVBTStrategy):
    name = "pin_bar_reversal"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "donchian_period": 20,
        "wick_ratio": 2.0,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        bull, _ = detect_pin_bar(ohlcv, wick_ratio=self.params["wick_ratio"])
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_period"])
        near_low = ohlcv["low"] <= lo * 1.01
        return (bull & near_low).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] > ema).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, bear = detect_pin_bar(ohlcv, wick_ratio=self.params["wick_ratio"])
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["donchian_period"])
        near_high = ohlcv["high"] >= up * 0.99
        return (bear & near_high).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        ema = compute_ema(ohlcv["close"], 20)
        return (ohlcv["close"] < ema).fillna(False)


# ---------------------------------------------------------------------------
# #37 StochasticReversal
# ---------------------------------------------------------------------------
class StochasticReversal(BaseVBTStrategy):
    name = "stochastic_reversal"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "k_period": 14,
        "d_period": 3,
        "smooth_k": 3,
        "oversold": 20,
        "overbought": 80,
        "ema_period": 200,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        k, d = compute_stochastic(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["k_period"], self.params["d_period"], self.params["smooth_k"],
        )
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        cross_up = (k > d) & (k.shift(1) <= d.shift(1)) & (k.shift(1) < self.params["oversold"])
        return (cross_up & (ohlcv["close"] > ema)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        k, d = compute_stochastic(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["k_period"], self.params["d_period"], self.params["smooth_k"],
        )
        return (k > self.params["overbought"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        k, d = compute_stochastic(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["k_period"], self.params["d_period"], self.params["smooth_k"],
        )
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        cross_dn = (k < d) & (k.shift(1) >= d.shift(1)) & (k.shift(1) > self.params["overbought"])
        return (cross_dn & (ohlcv["close"] < ema)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        k, d = compute_stochastic(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["k_period"], self.params["d_period"], self.params["smooth_k"],
        )
        return (k < self.params["oversold"]).fillna(False)


# ---------------------------------------------------------------------------
# #38 MeanReversionKalman — EWMA filter 近似 Kalman
# ---------------------------------------------------------------------------
class MeanReversionKalman(BaseVBTStrategy):
    """Kalman filter 近似：使用快速 EWMA 作為 state estimate。"""

    name = "mr_kalman"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "fast_alpha": 0.1,
        "std_period": 50,
        "entry_sigma": 2.0,
        "exit_sigma": 0.5,
        "leverage": 1,
    }

    def _filter(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        alpha = self.params["fast_alpha"]
        state = close.ewm(alpha=alpha, adjust=False).mean()
        residual = close - state
        std = residual.rolling(self.params["std_period"]).std(ddof=0)
        return state, std

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        state, std = self._filter(ohlcv["close"])
        return (ohlcv["close"] < state - self.params["entry_sigma"] * std).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        state, std = self._filter(ohlcv["close"])
        return (ohlcv["close"] > state - self.params["exit_sigma"] * std).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        state, std = self._filter(ohlcv["close"])
        return (ohlcv["close"] > state + self.params["entry_sigma"] * std).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        state, std = self._filter(ohlcv["close"])
        return (ohlcv["close"] < state + self.params["exit_sigma"] * std).fillna(False)


# ---------------------------------------------------------------------------
# #39 GapFade — 日線跳空回補
# ---------------------------------------------------------------------------
class GapFade(BaseVBTStrategy):
    """跳空 > threshold 時反向進場，目標回補昨收。"""

    name = "gap_fade"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "gap_threshold": 0.02,
        "leverage": 1,
    }

    def _gap(self, ohlcv: pd.DataFrame) -> pd.Series:
        prev_close = ohlcv["close"].shift(1)
        return (ohlcv["open"] - prev_close) / prev_close

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Gap down → long to fade
        gap = self._gap(ohlcv)
        return (gap < -self.params["gap_threshold"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        prev_close = ohlcv["close"].shift(1)
        return (ohlcv["close"] >= prev_close).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Gap up → short to fade
        gap = self._gap(ohlcv)
        return (gap > self.params["gap_threshold"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        prev_close = ohlcv["close"].shift(1)
        return (ohlcv["close"] <= prev_close).fillna(False)
