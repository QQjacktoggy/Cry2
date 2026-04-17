"""Arb / cross-asset / walk-forward family (#46-#50)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.bollinger import compute_ema
from backtest_tool.strategies.indicators.donchian import compute_donchian


# ---------------------------------------------------------------------------
# #46 FundingArbRelaxed — 放寬進場門檻、縮短持倉
# ---------------------------------------------------------------------------
class FundingArbRelaxed(FundingArbVBT):
    name = "funding_arb_relaxed"
    default_params: dict[str, Any] = {
        "funding_rate_threshold_annual": 10.0,
        "funding_rate_exit_annual": 3.0,
        "max_hold_days": 3,
        "leverage": 1,
    }
    required_timeframe = "8h"


# ---------------------------------------------------------------------------
# #47 SpotPerpBasis — delta-neutral basis arb（需要 basis column；否則退化成訊號）
# ---------------------------------------------------------------------------
class SpotPerpBasis(BaseVBTStrategy):
    """若 OHLCV DataFrame 有 'basis_bps' 欄位：basis > threshold 時做多現貨放空永續。
    單機無法同時操作兩個市場——此處以 futures leg 近似：basis 過大時 short futures。
    """

    name = "spot_perp_basis"
    required_timeframe = "1h"
    default_params: dict[str, Any] = {
        "entry_bps": 30.0,
        "exit_bps": 5.0,
        "leverage": 1,
    }

    def _basis(self, ohlcv: pd.DataFrame) -> pd.Series:
        if "basis_bps" in ohlcv.columns:
            return ohlcv["basis_bps"]
        # Fallback: use funding_rate × 10000 作為 basis 近似
        if "funding_rate" in ohlcv.columns:
            return ohlcv["funding_rate"] * 10000.0
        return pd.Series(0.0, index=ohlcv.index)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=ohlcv.index)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=ohlcv.index)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        basis = self._basis(ohlcv)
        return (basis > self.params["entry_bps"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        basis = self._basis(ohlcv)
        return (basis.abs() < self.params["exit_bps"]).fillna(False)


# ---------------------------------------------------------------------------
# #48 LongHorizonETH — 1D Donchian，針對 ETH 專用
# ---------------------------------------------------------------------------
class LongHorizonETH(BaseVBTStrategy):
    """ETH 專用長週期策略——週期拉長克服短週期 ETH 噪訊。"""

    name = "long_horizon_eth"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "entry_period": 40,
        "exit_period": 20,
        "ema_period": 100,
        "adx_threshold": 20,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] > up) & (adx > self.params["adx_threshold"]) & (ohlcv["close"] > ema)
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] < lo) & (adx > self.params["adx_threshold"]) & (ohlcv["close"] < ema)
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #49 LongHorizonSOL — SOL 專用版本，較短週期配合 SOL 較高波動
# ---------------------------------------------------------------------------
class LongHorizonSOL(BaseVBTStrategy):
    """SOL 專用：較短週期（波動較高可能需要較快反應）+ ATR 濾波。"""

    name = "long_horizon_sol"
    required_timeframe = "1d"
    default_params: dict[str, Any] = {
        "entry_period": 25,
        "exit_period": 12,
        "ema_period": 50,
        "adx_threshold": 20,
        "leverage": 1,
    }

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] > up) & (adx > self.params["adx_threshold"]) & (ohlcv["close"] > ema)
        ).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["entry_period"])
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        ema = compute_ema(ohlcv["close"], self.params["ema_period"])
        return (
            (ohlcv["close"] < lo) & (adx > self.params["adx_threshold"]) & (ohlcv["close"] < ema)
        ).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["exit_period"])
        return (ohlcv["close"] > up).fillna(False)


# ---------------------------------------------------------------------------
# #50 WalkForwardTrend — Walk-forward 自適應：用前 N 個月的最佳 entry_period
# ---------------------------------------------------------------------------
class WalkForwardTrend(BaseVBTStrategy):
    """簡化 walk-forward：以前 M 根 bar 的波動度等比自動調整 entry_period。
    代表「在樣本內找到的最佳參數、用於樣本外」的一種穩健形式。"""

    name = "walk_forward_trend"
    required_timeframe = "4h"
    default_params: dict[str, Any] = {
        "base_entry": 20,
        "base_exit": 10,
        "train_bars": 500,
        "leverage": 1,
    }

    def _adaptive_entry(self, ohlcv: pd.DataFrame) -> pd.Series:
        """根據近 train_bars 期間的「close 波動」縮放 entry_period。"""
        base = self.params["base_entry"]
        lb = self.params["train_bars"]
        realized_vol = ohlcv["close"].pct_change().rolling(lb).std(ddof=0)
        # 低波動延長、（避免過度拉長）上限 base*2，下限 base*0.5
        median_vol = realized_vol.expanding().median()
        ratio = (median_vol / realized_vol).clip(0.5, 2.0)
        # 以 rolling 高低估計通道（近似變動 lookback）
        period_floor = max(5, int(base * 0.5))
        period_ceil = int(base * 2)
        up_short = ohlcv["high"].rolling(period_floor).max().shift(1)
        up_mid = ohlcv["high"].rolling(base).max().shift(1)
        up_long = ohlcv["high"].rolling(period_ceil).max().shift(1)
        up = up_mid.copy()
        up[ratio < 0.8] = up_short[ratio < 0.8]
        up[ratio > 1.3] = up_long[ratio > 1.3]
        lo_short = ohlcv["low"].rolling(period_floor).min().shift(1)
        lo_mid = ohlcv["low"].rolling(base).min().shift(1)
        lo_long = ohlcv["low"].rolling(period_ceil).min().shift(1)
        lo = lo_mid.copy()
        lo[ratio < 0.8] = lo_short[ratio < 0.8]
        lo[ratio > 1.3] = lo_long[ratio > 1.3]
        return up, lo

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _ = self._adaptive_entry(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] > up) & (adx > 20)).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["base_exit"])
        return (ohlcv["close"] < lo).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        _, lo = self._adaptive_entry(ohlcv)
        adx = compute_adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
        return ((ohlcv["close"] < lo) & (adx > 20)).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        up, _, _ = compute_donchian(ohlcv["high"], ohlcv["low"], self.params["base_exit"])
        return (ohlcv["close"] > up).fillna(False)
