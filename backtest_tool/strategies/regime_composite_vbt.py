"""Regime-aware composite VBT strategy with daily profit target simulation.

Routes to:
  TRENDING  → TrendDonchianMTF   (aggressive_leverage_trending, default 7x)
  RANGING   → MeanReversionBBVBT (aggressive_leverage_ranging,  default 3x)
  NEUTRAL   → MeanReversionBBVBT (aggressive_leverage_neutral,  default 2x,
                                   new entries skipped when skip_neutral=True)

Once the simulated running daily PnL exceeds daily_profit_target_usd the
strategy switches to conservative_leverage (1x) and conservative_size_factor
(0.25x) for the rest of that UTC calendar day. State resets at UTC midnight.

Run via:
    python -m backtest_tool.engine.runner \\
        --strategy regime_composite --symbols BTCUSDT ETHUSDT --tf 15m
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Simple rolling ADX approximation for regime detection in backtest."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = (-low.diff())
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    smooth = period
    tr_smooth = tr.ewm(span=smooth, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=smooth, adjust=False).mean() / tr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=smooth, adjust=False).mean() / tr_smooth.replace(0, np.nan)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(span=smooth, adjust=False).mean()
    return adx.fillna(0.0)


def _classify_regime(adx: pd.Series, trend_thr: float = 25.0, range_thr: float = 20.0) -> pd.Series:
    """Classify each bar as 'trending', 'ranging', or 'neutral'."""
    regime = pd.Series("neutral", index=adx.index)
    regime[adx >= trend_thr] = "trending"
    regime[adx <= range_thr] = "ranging"
    return regime


def _compute_bb(close: pd.Series, period: int = 20, std: float = 2.0):
    mid = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    return mid + std * sigma, mid, mid - std * sigma


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _compute_donchian(close: pd.Series, period: int = 20):
    return close.rolling(period).max(), close.rolling(period).min()


class RegimeCompositeVBTStrategy(BaseVBTStrategy):
    """Regime-aware composite VBT strategy with daily profit target simulation."""

    name = "regime_composite"
    default_params: dict[str, Any] = {
        # Regime detection
        "adx_period": 14,
        "adx_trend_threshold": 25.0,
        "adx_range_threshold": 20.0,
        # Aggressive leverage per regime
        "aggressive_leverage_trending": 7,
        "aggressive_leverage_ranging": 3,
        "aggressive_leverage_neutral": 2,
        # Conservative mode
        "daily_profit_target_usd": 20.0,
        "conservative_leverage": 1,
        "conservative_size_factor": 0.25,
        "allocation_usd": 110.0,
        "skip_neutral_entries": True,
        # Sub-strategy params
        "dc_period": 10,          # Donchian breakout period (trending)
        "bb_period": 20,          # Bollinger period (ranging/neutral)
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "sl_pct": 0.015,          # Hard stop-loss fraction
    }
    required_timeframe = "15m"

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long entries: breakout when trending, BB lower when ranging."""
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])

        # Trending: Donchian breakout long
        dc_high, _ = _compute_donchian(ohlcv["close"], p["dc_period"])
        trend_entry = ohlcv["close"] >= dc_high.shift(1)

        # Ranging / Neutral: BB lower + RSI oversold
        bb_upper, bb_mid, bb_lower = _compute_bb(ohlcv["close"], p["bb_period"], p["bb_std"])
        rsi = _compute_rsi(ohlcv["close"], p["rsi_period"])
        mr_entry = (ohlcv["close"] <= bb_lower) & (rsi < p["rsi_oversold"])

        entries = pd.Series(False, index=ohlcv.index)
        entries[regime == "trending"] = trend_entry[regime == "trending"]
        entries[regime == "ranging"] = mr_entry[regime == "ranging"]
        if not p["skip_neutral_entries"]:
            entries[regime == "neutral"] = mr_entry[regime == "neutral"]

        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long exits: Donchian lower when trending, BB middle when ranging."""
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])

        _, dc_low = _compute_donchian(ohlcv["close"], p["dc_period"])
        trend_exit = ohlcv["close"] <= dc_low.shift(1)

        _, bb_mid, _ = _compute_bb(ohlcv["close"], p["bb_period"], p["bb_std"])
        mr_exit = ohlcv["close"] >= bb_mid

        exits = pd.Series(False, index=ohlcv.index)
        exits[regime == "trending"] = trend_exit[regime == "trending"]
        exits[(regime == "ranging") | (regime == "neutral")] = mr_exit[
            (regime == "ranging") | (regime == "neutral")
        ]
        return exits.fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short entries: Donchian breakdown when trending, BB upper when ranging."""
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])

        _, dc_low = _compute_donchian(ohlcv["close"], p["dc_period"])
        trend_short = ohlcv["close"] <= dc_low.shift(1)

        bb_upper, _, _ = _compute_bb(ohlcv["close"], p["bb_period"], p["bb_std"])
        rsi = _compute_rsi(ohlcv["close"], p["rsi_period"])
        mr_short = (ohlcv["close"] >= bb_upper) & (rsi > p["rsi_overbought"])

        short_entries = pd.Series(False, index=ohlcv.index)
        short_entries[regime == "trending"] = trend_short[regime == "trending"]
        short_entries[regime == "ranging"] = mr_short[regime == "ranging"]
        if not p["skip_neutral_entries"]:
            short_entries[regime == "neutral"] = mr_short[regime == "neutral"]

        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short exits: Donchian upper when trending, BB middle when ranging."""
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])

        dc_high, _ = _compute_donchian(ohlcv["close"], p["dc_period"])
        trend_short_exit = ohlcv["close"] >= dc_high.shift(1)

        _, bb_mid, _ = _compute_bb(ohlcv["close"], p["bb_period"], p["bb_std"])
        mr_short_exit = ohlcv["close"] <= bb_mid

        short_exits = pd.Series(False, index=ohlcv.index)
        short_exits[regime == "trending"] = trend_short_exit[regime == "trending"]
        short_exits[(regime == "ranging") | (regime == "neutral")] = mr_short_exit[
            (regime == "ranging") | (regime == "neutral")
        ]
        return short_exits.fillna(False)

    # ── Daily target simulation helpers ─────────────────────────────────────

    def compute_effective_leverage_series(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Return per-bar effective leverage after simulating the daily target gate.

        This is informational — use it to understand how often conservative mode
        would have activated.  It does NOT retroactively modify entry/exit signals
        (signal generation is separate to stay compatible with BaseVBTStrategy).
        """
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])
        entries = self.generate_entries(ohlcv)
        exits = self.generate_exits(ohlcv)

        lev_map = {
            "trending": p["aggressive_leverage_trending"],
            "ranging": p["aggressive_leverage_ranging"],
            "neutral": p["aggressive_leverage_neutral"],
        }

        allocation = p["allocation_usd"]
        target = p["daily_profit_target_usd"]
        sl_pct = p["sl_pct"]

        effective_lev = pd.Series(1, index=ohlcv.index, dtype=float)
        daily_pnl: float = 0.0
        conservative: bool = False
        prev_date = None
        in_pos: bool = False
        entry_price: float = 0.0
        entry_lev: float = 1.0

        for ts, row in ohlcv.iterrows():
            bar_date = ts.date() if hasattr(ts, "date") else None
            if bar_date and bar_date != prev_date:
                daily_pnl = 0.0
                conservative = False
                prev_date = bar_date

            if target > 0 and daily_pnl >= target and not conservative:
                conservative = True

            if conservative:
                lev = float(p["conservative_leverage"])
            else:
                lev = float(lev_map.get(regime.loc[ts], 1))
            effective_lev.loc[ts] = lev

            # Simulate simple PnL for daily gate
            if exits.loc[ts] and in_pos and entry_price > 0:
                trade_pnl = (row["close"] - entry_price) / entry_price * allocation * entry_lev
                daily_pnl += trade_pnl
                in_pos = False
                entry_price = 0.0

            if entries.loc[ts] and not in_pos:
                size_factor = p["conservative_size_factor"] if conservative else 1.0
                in_pos = True
                entry_price = row["close"]
                entry_lev = lev * size_factor

        return effective_lev

    def get_regime_stats(self, ohlcv: pd.DataFrame) -> dict:
        """Return a summary of regime distribution for diagnostics."""
        p = self.params
        adx = _compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], p["adx_period"])
        regime = _classify_regime(adx, p["adx_trend_threshold"], p["adx_range_threshold"])
        counts = regime.value_counts(normalize=True) * 100
        return {
            "trending_pct": round(counts.get("trending", 0.0), 1),
            "ranging_pct": round(counts.get("ranging", 0.0), 1),
            "neutral_pct": round(counts.get("neutral", 0.0), 1),
            "total_bars": len(regime),
        }
