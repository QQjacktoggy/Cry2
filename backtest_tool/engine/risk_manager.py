"""Risk management module: portfolio-level and strategy-level risk controls.

Phase 3 implementation:
  3A: Portfolio-level drawdown stop (pause all trading if DD > threshold)
  3B: Dynamic position sizing (fixed-fraction + ATR-based)
  3C: Volatility-adaptive leverage (ATR percentile → leverage scaling)
  3D: Consecutive loss protection (pause after N consecutive losses)
  3E: Maximum holding time limit (force exit after N bars)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backtest_tool.strategies.indicators.atr import compute_atr


# =============================================================================
# 3A: Portfolio-Level Drawdown Stop
# =============================================================================


def apply_portfolio_stop(
    equity: pd.Series,
    entries: pd.Series,
    exits: pd.Series,
    max_dd_pct: float = 0.20,
    cooldown_bars: int = 48,
    short_entries: pd.Series | None = None,
    short_exits: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series | None, pd.Series | None]:
    """Pause all trading when portfolio drawdown exceeds threshold.

    Args:
        equity: Portfolio equity curve (or close price as proxy).
        entries: Long entry signals.
        exits: Long exit signals.
        max_dd_pct: Maximum drawdown fraction to trigger stop (e.g., 0.20 = 20%).
        cooldown_bars: Number of bars to stay paused after trigger.
        short_entries: Short entry signals (optional).
        short_exits: Short exit signals (optional).

    Returns:
        Filtered (entries, exits, short_entries, short_exits).
    """
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax

    # Find bars where drawdown exceeds threshold
    is_stopped = pd.Series(False, index=equity.index)
    stop_until = -1

    for i in range(len(equity)):
        if drawdown.iloc[i] < -max_dd_pct:
            stop_until = i + cooldown_bars
        if i <= stop_until:
            is_stopped.iloc[i] = True

    # Filter signals
    entries_out = entries & ~is_stopped
    exits_out = exits | is_stopped  # Force exit when stopped

    se_out = None
    sx_out = None
    if short_entries is not None:
        se_out = short_entries & ~is_stopped
    if short_exits is not None:
        sx_out = short_exits | is_stopped

    return entries_out, exits_out, se_out, sx_out


# =============================================================================
# 3B: Dynamic Position Sizing (ATR-based)
# =============================================================================

@dataclass
class PositionSizer:
    """Compute position sizes based on volatility (ATR).

    Higher ATR → smaller position, lower ATR → larger position.
    Bounded by [min_fraction, max_fraction].
    """

    risk_per_trade: float = 0.02  # 2% of capital risked per trade
    atr_period: int = 14
    atr_multiplier: float = 2.0  # SL distance = ATR * multiplier
    min_fraction: float = 0.1   # Min position = 10% of capital
    max_fraction: float = 0.5   # Max position = 50% of capital

    def compute_sizes(
        self,
        ohlcv: pd.DataFrame,
        entries: pd.Series,
        capital: float = 10000.0,
    ) -> pd.Series:
        """Compute position size (fraction of capital) for each entry bar.

        Args:
            ohlcv: OHLCV DataFrame.
            entries: Boolean entry signals.
            capital: Current capital.

        Returns:
            Series of position fractions (0-1) for each bar.
        """
        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.atr_period)
        close = ohlcv["close"]

        # SL distance in price terms
        sl_distance = atr * self.atr_multiplier

        # Position size = (risk_amount) / sl_distance
        risk_amount = capital * self.risk_per_trade
        position_value = risk_amount / (sl_distance / close).replace(0, np.nan)
        fraction = (position_value / capital).clip(self.min_fraction, self.max_fraction)

        # Only return sizes where there are entries
        return fraction.where(entries, 0.0).fillna(0.0)


# =============================================================================
# 3C: Volatility-Adaptive Leverage
# =============================================================================

def compute_adaptive_leverage(
    ohlcv: pd.DataFrame,
    atr_period: int = 14,
    atr_lookback: int = 100,
    max_leverage: float = 3.0,
    min_leverage: float = 1.0,
    high_vol_pct: float = 80,
    low_vol_pct: float = 20,
) -> pd.Series:
    """Compute per-bar leverage based on ATR percentile.

    High volatility → low leverage, low volatility → high leverage.

    Args:
        ohlcv: OHLCV DataFrame.
        atr_period: ATR period.
        atr_lookback: Rolling window for percentile.
        max_leverage: Maximum allowed leverage (low vol).
        min_leverage: Minimum leverage (high vol).
        high_vol_pct: ATR percentile above which → min leverage.
        low_vol_pct: ATR percentile below which → max leverage.

    Returns:
        Series of leverage values per bar.
    """
    atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], atr_period)
    atr_pct = atr.rolling(atr_lookback).rank(pct=True) * 100

    # Linear interpolation between min and max leverage
    leverage = pd.Series(np.nan, index=ohlcv.index)
    leverage = np.where(
        atr_pct >= high_vol_pct,
        min_leverage,
        np.where(
            atr_pct <= low_vol_pct,
            max_leverage,
            max_leverage - (max_leverage - min_leverage) * (atr_pct - low_vol_pct) / (high_vol_pct - low_vol_pct),
        ),
    )
    return pd.Series(leverage, index=ohlcv.index).fillna(min_leverage)


# =============================================================================
# 3D: Consecutive Loss Protection
# =============================================================================

def apply_consecutive_loss_filter(
    entries: pd.Series,
    exits: pd.Series,
    close: pd.Series,
    max_consecutive_losses: int = 5,
    cooldown_bars: int = 24,
    short_entries: pd.Series | None = None,
    short_exits: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series | None, pd.Series | None]:
    """Pause trading after N consecutive losses.

    Tracks PnL of each completed trade (entry→exit) and pauses
    if consecutive losses reach the threshold.

    Args:
        entries: Long entry signals.
        exits: Long exit signals.
        close: Close price series.
        max_consecutive_losses: Trigger threshold.
        cooldown_bars: Bars to pause after trigger.
        short_entries: Short entry signals (optional).
        short_exits: Short exit signals (optional).

    Returns:
        Filtered signals.
    """
    n = len(entries)
    is_paused = pd.Series(False, index=entries.index)
    consec_losses = 0
    in_trade = False
    entry_price = 0.0
    pause_until = -1

    for i in range(n):
        if i <= pause_until:
            is_paused.iloc[i] = True
            continue

        if not in_trade and entries.iloc[i]:
            in_trade = True
            entry_price = close.iloc[i]
        elif in_trade and exits.iloc[i]:
            pnl = close.iloc[i] - entry_price
            in_trade = False
            if pnl < 0:
                consec_losses += 1
            else:
                consec_losses = 0
            if consec_losses >= max_consecutive_losses:
                pause_until = i + cooldown_bars
                consec_losses = 0

    entries_out = entries & ~is_paused
    exits_out = exits

    se_out = short_entries & ~is_paused if short_entries is not None else None
    sx_out = short_exits if short_exits is not None else None

    return entries_out, exits_out, se_out, sx_out


# =============================================================================
# 3E: Maximum Holding Time Limit
# =============================================================================

def apply_max_hold_limit(
    entries: pd.Series,
    exits: pd.Series,
    max_hold_bars: int = 120,
    short_entries: pd.Series | None = None,
    short_exits: pd.Series | None = None,
    short_max_hold_bars: int | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series | None, pd.Series | None]:
    """Force exit after maximum holding period.

    Args:
        entries: Long entry signals.
        exits: Long exit signals.
        max_hold_bars: Max bars for long positions (default 120 = ~20d at 4h).
        short_entries: Short entry signals (optional).
        short_exits: Short exit signals (optional).
        short_max_hold_bars: Max bars for short positions (default = max_hold_bars).

    Returns:
        Signals with forced exits added.
    """
    s_max = short_max_hold_bars or max_hold_bars
    n = len(entries)

    exits_out = exits.copy()
    in_long = False
    bars_held = 0

    for i in range(n):
        if not in_long and entries.iloc[i]:
            in_long = True
            bars_held = 0
        elif in_long:
            bars_held += 1
            if exits_out.iloc[i]:
                in_long = False
                bars_held = 0
            elif bars_held >= max_hold_bars:
                exits_out.iloc[i] = True
                in_long = False
                bars_held = 0

    sx_out = None
    if short_entries is not None and short_exits is not None:
        sx_out = short_exits.copy()
        in_short = False
        bars_held = 0
        for i in range(n):
            if not in_short and short_entries.iloc[i]:
                in_short = True
                bars_held = 0
            elif in_short:
                bars_held += 1
                if sx_out.iloc[i]:
                    in_short = False
                    bars_held = 0
                elif bars_held >= s_max:
                    sx_out.iloc[i] = True
                    in_short = False
                    bars_held = 0

    return entries, exits_out, short_entries, sx_out


# =============================================================================
# Composite Risk Manager — wraps all Phase 3 controls
# =============================================================================

@dataclass
class RiskManager:
    """Composite risk manager applying all Phase 3 controls.

    Usage:
        rm = RiskManager(max_dd_pct=0.20, max_consec_losses=5)
        entries, exits, se, sx = rm.apply(ohlcv, entries, exits, se, sx)
    """

    # 3A: Portfolio stop
    enable_portfolio_stop: bool = True
    max_dd_pct: float = 0.20
    dd_cooldown_bars: int = 48

    # 3B: Position sizing
    enable_position_sizing: bool = True
    risk_per_trade: float = 0.02
    atr_sl_mult: float = 2.0
    min_position_frac: float = 0.1
    max_position_frac: float = 0.5

    # 3C: Adaptive leverage
    enable_adaptive_leverage: bool = True
    max_leverage: float = 3.0
    min_leverage: float = 1.0
    high_vol_pct: float = 80.0
    low_vol_pct: float = 20.0

    # 3D: Consecutive loss protection
    enable_consec_loss_filter: bool = True
    max_consec_losses: int = 5
    loss_cooldown_bars: int = 24

    # 3E: Max hold time
    enable_max_hold: bool = True
    long_max_hold_bars: int = 120   # ~20d at 4h
    short_max_hold_bars: int = 168  # ~7d at 1h or ~28d at 4h

    def apply(
        self,
        ohlcv: pd.DataFrame,
        entries: pd.Series,
        exits: pd.Series,
        short_entries: pd.Series | None = None,
        short_exits: pd.Series | None = None,
        capital: float = 10000.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series | None, pd.Series | None]:
        """Apply all enabled risk controls sequentially.

        Returns:
            Filtered (entries, exits, short_entries, short_exits).
        """
        # 3E: Max hold time (add forced exits)
        if self.enable_max_hold:
            entries, exits, short_entries, short_exits = apply_max_hold_limit(
                entries, exits, self.long_max_hold_bars,
                short_entries, short_exits, self.short_max_hold_bars,
            )

        # 3D: Consecutive loss filter
        if self.enable_consec_loss_filter:
            entries, exits, short_entries, short_exits = apply_consecutive_loss_filter(
                entries, exits, ohlcv["close"],
                self.max_consec_losses, self.loss_cooldown_bars,
                short_entries, short_exits,
            )

        # 3A: Portfolio stop (using close as equity proxy)
        if self.enable_portfolio_stop:
            entries, exits, short_entries, short_exits = apply_portfolio_stop(
                ohlcv["close"], entries, exits,
                self.max_dd_pct, self.dd_cooldown_bars,
                short_entries, short_exits,
            )

        return entries, exits, short_entries, short_exits

    def get_adaptive_leverage(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Compute per-bar adaptive leverage (3C)."""
        if not self.enable_adaptive_leverage:
            return pd.Series(self.min_leverage, index=ohlcv.index)
        return compute_adaptive_leverage(
            ohlcv, max_leverage=self.max_leverage, min_leverage=self.min_leverage,
            high_vol_pct=self.high_vol_pct, low_vol_pct=self.low_vol_pct,
        )

    def get_position_sizes(
        self, ohlcv: pd.DataFrame, entries: pd.Series, capital: float = 10000.0,
    ) -> pd.Series:
        """Compute position sizes (3B)."""
        if not self.enable_position_sizing:
            return pd.Series(self.max_position_frac, index=ohlcv.index)
        sizer = PositionSizer(
            risk_per_trade=self.risk_per_trade,
            atr_multiplier=self.atr_sl_mult,
            min_fraction=self.min_position_frac,
            max_fraction=self.max_position_frac,
        )
        return sizer.compute_sizes(ohlcv, entries, capital)

    def summary(self) -> dict[str, Any]:
        """Return current risk config as dict."""
        return {
            "portfolio_stop": self.enable_portfolio_stop,
            "max_dd_pct": self.max_dd_pct,
            "position_sizing": self.enable_position_sizing,
            "adaptive_leverage": self.enable_adaptive_leverage,
            "consec_loss_filter": self.enable_consec_loss_filter,
            "max_hold": self.enable_max_hold,
        }
