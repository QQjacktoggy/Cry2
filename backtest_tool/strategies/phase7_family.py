"""Phase 7 new strategies (7A-7J): 10 new directions for diversification.

Covers: momentum rotation, trend-strength sizing, dual-channel breakout,
volatility mean-reversion, gamma-scalping grid, funding contrarian,
price-volume divergence, time-of-day filter, pairs trading, tail-risk hedge.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_ema
from backtest_tool.strategies.indicators.donchian import compute_donchian
from backtest_tool.strategies.indicators.keltner import compute_keltner
from backtest_tool.strategies.indicators.obv import compute_mfi, compute_obv
from backtest_tool.strategies.indicators.stoch import compute_roc


# ---------------------------------------------------------------------------
# 7A — Multi-Asset Momentum Rotation (single-symbol proxy)
# ---------------------------------------------------------------------------
class MomentumRotation(BaseVBTStrategy):
    """Weekly momentum rotation: go long when this asset's ROC is in the top
    percentile of its own rolling history, implying it's 'the strongest'.
    Enhanced version of MomentumRanking with weekly rebalance logic."""

    name = "momentum_rotation"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "roc_period": 14,
        "lookback": 60,
        "rebalance_days": 7,
        "upper_pct": 65,
        "lower_pct": 35,
        "leverage": 1,
    }

    def _weekly_mask(self, idx: pd.DatetimeIndex) -> pd.Series:
        """Only allow signals on rebalance days."""
        day_of_year = idx.dayofyear
        interval = self.params["rebalance_days"]
        return pd.Series((day_of_year % interval == 0), index=idx)

    def _pct_rank(self, close: pd.Series) -> pd.Series:
        roc = compute_roc(close, self.params["roc_period"])
        return roc.rolling(self.params["lookback"]).rank(pct=True) * 100

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        pct = self._pct_rank(ohlcv["close"])
        mask = self._weekly_mask(ohlcv.index)
        return ((pct > self.params["upper_pct"]) & mask).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        pct = self._pct_rank(ohlcv["close"])
        return (pct < 50).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        pct = self._pct_rank(ohlcv["close"])
        mask = self._weekly_mask(ohlcv.index)
        return ((pct < self.params["lower_pct"]) & mask).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        pct = self._pct_rank(ohlcv["close"])
        return (pct > 50).fillna(False)


# ---------------------------------------------------------------------------
# 7B — Trend Strength Sizing (Donchian + ADX-proportional entries)
# ---------------------------------------------------------------------------
class TrendStrengthSizing(BaseVBTStrategy):
    """Donchian breakout but only enters when ADX indicates strong trend.
    Uses ADX value as a confidence gate: ADX < 25 → skip, 30 → enter,
    40+ → strong signal. Approximates position sizing via entry filtering."""

    name = "trend_strength_sizing"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "entry_period": 20,
        "exit_period": 10,
        "adx_period": 14,
        "adx_min": 30,
        "adx_strong": 40,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return ((ohlcv["close"] > up) & (adx > self.params["adx_min"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return ((ohlcv["close"] < lo) & (adx > self.params["adx_min"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# 7C — Dual Channel Breakout (Donchian + Keltner double confirmation)
# ---------------------------------------------------------------------------
class DualChannelBreakout(BaseVBTStrategy):
    """Enter only when price breaks BOTH Donchian and Keltner channels.
    Drastically reduces false breakouts."""

    name = "dual_channel_breakout"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "dc_period": 20,
        "kc_ema": 20,
        "kc_atr": 10,
        "kc_mult": 2.0,
        "adx_period": 14,
        "adx_threshold": 25,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        dc_up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["dc_period"])
        kc_up, _, _ = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (ohlcv["close"] > dc_up)
            & (ohlcv["close"] > kc_up)
            & (adx > self.params["adx_threshold"])
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, kc_mid, _ = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        return (ohlcv["close"] < kc_mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, dc_lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["dc_period"])
        _, _, kc_lo = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["adx_period"])
        return (
            (ohlcv["close"] < dc_lo)
            & (ohlcv["close"] < kc_lo)
            & (adx > self.params["adx_threshold"])
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, kc_mid, _ = compute_keltner(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            self.params["kc_ema"], self.params["kc_atr"], self.params["kc_mult"],
        )
        return (ohlcv["close"] > kc_mid).fillna(False)


# ---------------------------------------------------------------------------
# 7D — Volatility Mean Reversion
# ---------------------------------------------------------------------------
class VolatilityMeanReversion(BaseVBTStrategy):
    """Trade volatility regime shifts: high ATR → expect contraction (MR mode),
    low ATR → expect expansion (breakout mode)."""

    name = "vol_mean_reversion"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "atr_period": 14,
        "atr_lookback": 100,
        "high_pct": 90,
        "low_pct": 10,
        "bb_period": 20,
        "bb_std": 2.0,
        "dc_period": 20,
        "leverage": 1,
    }

    def _atr_pct(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        return atr.rolling(self.params["atr_lookback"]).rank(pct=True) * 100

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr_pct = self._atr_pct(ohlcv)
        # High vol → MR: buy at BB lower
        _, _, bb_lo = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        mr_entry = (atr_pct > self.params["high_pct"]) & (ohlcv["close"] < bb_lo)
        # Low vol → Breakout: buy at Donchian upper
        dc_up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["dc_period"])
        bo_entry = (atr_pct < self.params["low_pct"]) & (ohlcv["close"] > dc_up)
        return (mr_entry | bo_entry).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, bb_mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] > bb_mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        atr_pct = self._atr_pct(ohlcv)
        bb_up, _, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        mr_short = (atr_pct > self.params["high_pct"]) & (ohlcv["close"] > bb_up)
        _, dc_lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["dc_period"])
        bo_short = (atr_pct < self.params["low_pct"]) & (ohlcv["close"] < dc_lo)
        return (mr_short | bo_short).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, bb_mid, _ = compute_bollinger(ohlcv["close"], self.params["bb_period"], self.params["bb_std"])
        return (ohlcv["close"] < bb_mid).fillna(False)


# ---------------------------------------------------------------------------
# 7E — Gamma Scalping (Regime-Adaptive Grid)
# ---------------------------------------------------------------------------
class GammaScalpingGrid(BaseVBTStrategy):
    """High vol → tight mean-reversion (gamma scalping), low vol → wide trend grid.
    Uses ATR to adapt BB width for entries."""

    name = "gamma_scalping"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "atr_period": 14,
        "atr_lookback": 100,
        "vol_threshold": 60,
        "bb_period": 20,
        "bb_std_tight": 1.5,
        "bb_std_wide": 3.0,
        "leverage": 1,
    }

    def _adaptive_bb(self, ohlcv: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.params["atr_period"])
        atr_pct = atr.rolling(self.params["atr_lookback"]).rank(pct=True) * 100
        is_high_vol = atr_pct > self.params["vol_threshold"]
        std_tight = self.params["bb_std_tight"]
        std_wide = self.params["bb_std_wide"]
        bb_std = pd.Series(np.where(is_high_vol, std_tight, std_wide), index=ohlcv.index)
        mid = ohlcv["close"].rolling(self.params["bb_period"]).mean()
        rolling_std = ohlcv["close"].rolling(self.params["bb_period"]).std(ddof=0)
        upper = mid + bb_std * rolling_std
        lower = mid - bb_std * rolling_std
        return upper, mid, lower

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, _, lower = self._adaptive_bb(ohlcv)
        return (ohlcv["close"] < lower).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = self._adaptive_bb(ohlcv)
        return (ohlcv["close"] > mid).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        upper, _, _ = self._adaptive_bb(ohlcv)
        return (ohlcv["close"] > upper).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, mid, _ = self._adaptive_bb(ohlcv)
        return (ohlcv["close"] < mid).fillna(False)


# ---------------------------------------------------------------------------
# 7F — Funding Rate Contrarian
# ---------------------------------------------------------------------------
class FundingContrarian(BaseVBTStrategy):
    """Contrarian funding rate: when crowd is over-leveraged long (high positive
    funding), go short; when crowd is over-leveraged short (negative funding),
    go long. In the absence of funding data, uses price momentum as a proxy."""

    name = "funding_contrarian"
    required_timeframe = "8h"
    default_params: dict[str, Any] = {
        "roc_period": 24,
        "overbought": 8.0,
        "oversold": -8.0,
        "exit_threshold": 0.0,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Proxy: extreme negative ROC → crowd panic → go long
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc < self.params["oversold"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc > self.params["exit_threshold"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc > self.params["overbought"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        roc = compute_roc(ohlcv["close"], self.params["roc_period"])
        return (roc < self.params["exit_threshold"]).fillna(False)


# ---------------------------------------------------------------------------
# 7G — Price-Volume Divergence
# ---------------------------------------------------------------------------
class PriceVolumeDivergence(BaseVBTStrategy):
    """Detect divergence between price and volume trends using OBV slope
    vs price slope. Divergence signals potential reversals."""

    name = "pv_divergence"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "slope_period": 20,
        "mfi_period": 14,
        "mfi_oversold": 20,
        "mfi_overbought": 80,
        "leverage": 1,
    }

    def _obv_slope(self, ohlcv: pd.DataFrame) -> pd.Series:
        obv = compute_obv(ohlcv["close"], ohlcv["volume"])
        return obv.diff(self.params["slope_period"])

    def _price_slope(self, ohlcv: pd.DataFrame) -> pd.Series:
        return ohlcv["close"].diff(self.params["slope_period"])

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Bullish divergence: price falling but OBV rising + MFI oversold
        ps = self._price_slope(ohlcv)
        os = self._obv_slope(ohlcv)
        mfi = compute_mfi(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          ohlcv["volume"], self.params["mfi_period"])
        return ((ps < 0) & (os > 0) & (mfi < self.params["mfi_oversold"])).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        mfi = compute_mfi(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          ohlcv["volume"], self.params["mfi_period"])
        return (mfi > 50).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Bearish divergence: price rising but OBV falling + MFI overbought
        ps = self._price_slope(ohlcv)
        os = self._obv_slope(ohlcv)
        mfi = compute_mfi(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          ohlcv["volume"], self.params["mfi_period"])
        return ((ps > 0) & (os < 0) & (mfi > self.params["mfi_overbought"])).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        mfi = compute_mfi(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          ohlcv["volume"], self.params["mfi_period"])
        return (mfi < 50).fillna(False)


# ---------------------------------------------------------------------------
# 7H — Time-of-Day Filter (overlay strategy)
# ---------------------------------------------------------------------------
class TimeOfDayFilter(BaseVBTStrategy):
    """Only trade during historically profitable hours. Uses EMA crossover
    as base signal, filtered by allowed trading hours."""

    name = "time_of_day_filter"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "fast_ema": 12,
        "slow_ema": 26,
        "allowed_hours_utc": [1, 2, 3, 7, 8, 9, 13, 14, 15],
        "leverage": 1,
    }

    def _hour_mask(self, idx: pd.DatetimeIndex) -> pd.Series:
        hours = idx.hour if hasattr(idx, "hour") else pd.to_datetime(idx).hour
        allowed = self.params["allowed_hours_utc"]
        return pd.Series(hours.isin(allowed) if hasattr(hours, "isin")
                         else [h in allowed for h in hours], index=idx)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        fast = compute_ema(ohlcv["close"], self.params["fast_ema"])
        slow = compute_ema(ohlcv["close"], self.params["slow_ema"])
        cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        mask = self._hour_mask(ohlcv.index)
        return (cross_up & mask).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        fast = compute_ema(ohlcv["close"], self.params["fast_ema"])
        slow = compute_ema(ohlcv["close"], self.params["slow_ema"])
        return (fast < slow).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        fast = compute_ema(ohlcv["close"], self.params["fast_ema"])
        slow = compute_ema(ohlcv["close"], self.params["slow_ema"])
        cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        mask = self._hour_mask(ohlcv.index)
        return (cross_down & mask).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        fast = compute_ema(ohlcv["close"], self.params["fast_ema"])
        slow = compute_ema(ohlcv["close"], self.params["slow_ema"])
        return (fast > slow).fillna(False)


# ---------------------------------------------------------------------------
# 7I — BTC-ETH Pairs Trading (single-symbol z-score proxy)
# ---------------------------------------------------------------------------
class PairsSpreadMR(BaseVBTStrategy):
    """In single-symbol mode, uses price-vs-rolling-mean z-score as a proxy
    for spread mean-reversion (analogous to pairs trading)."""

    name = "pairs_spread_mr"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "zscore_period": 60,
        "entry_z": 2.0,
        "exit_z": 0.5,
        "leverage": 1,
    }

    def _zscore(self, close: pd.Series) -> pd.Series:
        period = self.params["zscore_period"]
        mean = close.rolling(period).mean()
        std = close.rolling(period).std(ddof=0)
        return (close - mean) / std.replace(0, np.nan)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = self._zscore(ohlcv["close"])
        return (z < -self.params["entry_z"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = self._zscore(ohlcv["close"])
        return (z > -self.params["exit_z"]).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = self._zscore(ohlcv["close"])
        return (z > self.params["entry_z"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        z = self._zscore(ohlcv["close"])
        return (z < self.params["exit_z"]).fillna(False)


# ---------------------------------------------------------------------------
# 7J — Tail Risk Hedge
# ---------------------------------------------------------------------------
class TailRiskHedge(BaseVBTStrategy):
    """Small contrarian positions during extreme runs.
    Consecutive up days > N → small short (black swan protection).
    Consecutive down days > M → small long (panic reversal)."""

    name = "tail_risk_hedge"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "consec_up_threshold": 10,
        "consec_down_threshold": 5,
        "exit_bars": 5,
        "leverage": 1,
    }

    def _consecutive_direction(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        """Count consecutive up/down days."""
        change = close.diff()
        is_up = (change > 0).astype(int)
        is_down = (change < 0).astype(int)

        # Count consecutive
        up_count = is_up.copy()
        down_count = is_down.copy()
        for i in range(1, len(close)):
            if is_up.iloc[i]:
                up_count.iloc[i] = up_count.iloc[i - 1] + 1
            else:
                up_count.iloc[i] = 0
            if is_down.iloc[i]:
                down_count.iloc[i] = down_count.iloc[i - 1] + 1
            else:
                down_count.iloc[i] = 0

        return up_count, down_count

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Panic buy after consecutive drops
        _, down_count = self._consecutive_direction(ohlcv["close"])
        return (down_count >= self.params["consec_down_threshold"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Exit after N bars (simple time-based exit)
        entries = self.generate_entries(ohlcv)
        exit_bars = self.params["exit_bars"]
        exits = entries.shift(exit_bars).fillna(False)
        return exits.astype(bool)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        # Hedge short after consecutive rises
        up_count, _ = self._consecutive_direction(ohlcv["close"])
        return (up_count >= self.params["consec_up_threshold"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        short_entries = self.generate_short_entries(ohlcv)
        exit_bars = self.params["exit_bars"]
        exits = short_entries.shift(exit_bars).fillna(False)
        return exits.astype(bool)
