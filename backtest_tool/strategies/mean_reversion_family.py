"""Mean-reversion strategy family (#18-#25).

Fixes MeanReversionBB baseline (+9.4%, Sharpe 0.58, payoff < 1 → avg_win
$172 vs avg_loss $213). Adds regime filters, ATR stops, partial TP,
z-score, VWAP, divergence-based improvements.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema, compute_rsi
from backtest_tool.strategies.indicators.zscore import compute_vwap, compute_zscore


# ---------------------------------------------------------------------------
# #18 MRBBRegimeFiltered — only in ranging markets
# ---------------------------------------------------------------------------
class MRBBRegimeFiltered(BaseVBTStrategy):
    name = "mrbb_regime"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "adx_max": 20,
        "ema_period": 200,
        "ema_band": 0.1,
        "leverage": 1,
    }

    def _regime_ok(self, ohlcv: pd.DataFrame) -> pd.Series:
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        near = (ohlcv["close"] / ema - 1.0).abs() <= self.params["ema_band"]
        return (adx < self.params["adx_max"]) & near

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return (
            (ohlcv["close"] <= lo) & (rsi < self.params["rsi_oversold"]) & self._regime_ok(ohlcv)
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return (
            (ohlcv["close"] >= up) & (rsi > self.params["rsi_overbought"]) & self._regime_ok(ohlcv)
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #19 MRBBATRStop — ATR-sized stop via exit rule (approximation)
# ---------------------------------------------------------------------------
class MRBBATRStop(BaseVBTStrategy):
    """Exit = BB middle touched OR price crosses entry - 1.5*ATR (approx stop)."""

    name = "mrbb_atr_stop"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "atr_period": 14,
        "atr_mult": 1.5,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return ((ohlcv["close"] <= lo) & (rsi < self.params["rsi_oversold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        # ATR-stop approximation: exit if close drops more than atr_mult*ATR below BB middle
        stop = mid - self.params["atr_mult"] * atr
        return ((ohlcv["close"] >= mid) | (ohlcv["close"] < stop)).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return ((ohlcv["close"] >= up) & (rsi > self.params["rsi_overbought"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        stop = mid + self.params["atr_mult"] * atr
        return ((ohlcv["close"] <= mid) | (ohlcv["close"] > stop)).fillna(False)


# ---------------------------------------------------------------------------
# #20 MRBBMiddleExit — already BB middle but tightened: exit at 50% of move
# ---------------------------------------------------------------------------
class MRBBMiddleExit(BaseVBTStrategy):
    """Exit earlier (at 50% of BB middle-move) to lock payoff > 1."""

    name = "mrbb_middle_exit"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "exit_frac": 0.5,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return ((ohlcv["close"] <= lo) & (rsi < self.params["rsi_oversold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        target = lo + (mid - lo) * self.params["exit_frac"]
        return (ohlcv["close"] >= target).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], 14)
        return ((ohlcv["close"] >= up) & (rsi > self.params["rsi_overbought"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        target = up - (up - mid) * self.params["exit_frac"]
        return (ohlcv["close"] <= target).fillna(False)


# ---------------------------------------------------------------------------
# #21 MRBBRSIDivergence — price new low, RSI not
# ---------------------------------------------------------------------------
class MRBBRSIDivergence(BaseVBTStrategy):
    name = "mrbb_rsi_div"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "lookback": 10,
        "leverage": 1,
    }

    def _bull_div(self, close: pd.Series, rsi: pd.Series) -> pd.Series:
        lb = self.params["lookback"]
        price_new_low = close <= close.rolling(lb).min()
        rsi_prev_low = rsi.rolling(lb).min().shift(1)
        return price_new_low & (rsi > rsi_prev_low + 2)

    def _bear_div(self, close: pd.Series, rsi: pd.Series) -> pd.Series:
        lb = self.params["lookback"]
        price_new_high = close >= close.rolling(lb).max()
        rsi_prev_high = rsi.rolling(lb).max().shift(1)
        return price_new_high & (rsi < rsi_prev_high - 2)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return ((ohlcv["close"] <= lo) & self._bull_div(ohlcv["close"], rsi)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        rsi = compute_rsi(ohlcv["close"], self.params["rsi_period"])
        return ((ohlcv["close"] >= up) & self._bear_div(ohlcv["close"], rsi)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #22 MRBBVWAP — VWAP ± N*σ bands
# ---------------------------------------------------------------------------
class MRBBVWAP(BaseVBTStrategy):
    name = "mrbb_vwap"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "vwap_period": 24,
        "num_sigma": 2.0,
        "leverage": 1,
    }

    def _bands(self, ohlcv: pd.DataFrame):
        vwap, std = compute_vwap(
            ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"], self.params["vwap_period"]
        )
        k = self.params["num_sigma"]
        return vwap + k * std, vwap, vwap - k * std

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = self._bands(ohlcv)
        return (ohlcv["close"] <= lo).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = self._bands(ohlcv)
        return (ohlcv["close"] >= mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = self._bands(ohlcv)
        return (ohlcv["close"] >= up).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = self._bands(ohlcv)
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #23 MRBBZScore — z-score thresholds
# ---------------------------------------------------------------------------
class MRBBZScore(BaseVBTStrategy):
    name = "mrbb_zscore"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "period": 20,
        "entry_z": 2.0,
        "exit_z": 0.5,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = compute_zscore(ohlcv["close"], self.params["period"])
        return (z <= -self.params["entry_z"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = compute_zscore(ohlcv["close"], self.params["period"])
        return (z >= -self.params["exit_z"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = compute_zscore(ohlcv["close"], self.params["period"])
        return (z >= self.params["entry_z"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = compute_zscore(ohlcv["close"], self.params["period"])
        return (z <= self.params["exit_z"]).fillna(False)


# ---------------------------------------------------------------------------
# #24 MRBBDual — symmetric long/short BB (baseline was long-only by default)
# ---------------------------------------------------------------------------
class MRBBDual(BaseVBTStrategy):
    name = "mrbb_dual"
    required_timeframe = "1h"
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
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= up).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= mid).fillna(False)


# ---------------------------------------------------------------------------
# #25 MRBBPartialTP — two-tier exit: middle + opposite band
# ---------------------------------------------------------------------------
class MRBBPartialTP(BaseVBTStrategy):
    """Approximation of half-at-middle + half-at-opposite:
    we exit at opposite band (which would have been the second half).
    This lengthens holding period for larger payoff."""

    name = "mrbb_partial_tp"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "bb_period": 20,
        "bb_std": 2.0,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= lo).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= up).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, mid, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] >= up).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] <= lo).fillna(False)
