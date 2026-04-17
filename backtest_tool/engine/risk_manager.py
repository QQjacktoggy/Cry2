"""Risk management module: portfolio-level stop-loss and consecutive loss protection.

These classes generate boolean masks that can be applied to entry signals
to prevent trading during high-risk periods.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RiskEvent:
    """Record of a risk event."""

    timestamp: pd.Timestamp
    event_type: str
    details: dict


class PortfolioStopLoss:
    """Portfolio-level drawdown protection.

    When equity drawdown exceeds max_drawdown_pct, all trading is
    paused for cooldown_bars.

    Usage:
        psl = PortfolioStopLoss(max_drawdown_pct=20.0, cooldown_bars=48)
        mask = psl.generate_mask(equity_curve)
        filtered_entries = entries & mask
    """

    def __init__(self, max_drawdown_pct: float = 20.0, cooldown_bars: int = 48) -> None:
        """Initialize portfolio stop-loss.

        Args:
            max_drawdown_pct: Max drawdown % to trigger stop (e.g., 20.0 = 20%).
            cooldown_bars: Number of bars to pause trading after trigger.
        """
        self.max_drawdown_pct = max_drawdown_pct
        self.cooldown_bars = cooldown_bars
        self.events: list[RiskEvent] = []

    def generate_mask(self, equity: pd.Series) -> pd.Series:
        """Generate a boolean mask indicating when trading is allowed.

        Args:
            equity: Equity curve Series with DatetimeIndex.

        Returns:
            Boolean Series (True = trading allowed, False = paused).
        """
        if equity.empty:
            return pd.Series(dtype=bool)

        cummax = equity.cummax()
        drawdown_pct = ((equity - cummax) / cummax) * 100.0

        mask = pd.Series(True, index=equity.index)
        cooldown_remaining = 0

        for i in range(len(equity)):
            if cooldown_remaining > 0:
                mask.iloc[i] = False
                cooldown_remaining -= 1
                continue

            if drawdown_pct.iloc[i] <= -self.max_drawdown_pct:
                mask.iloc[i] = False
                cooldown_remaining = self.cooldown_bars - 1
                self.events.append(RiskEvent(
                    timestamp=equity.index[i],
                    event_type="portfolio_stop",
                    details={
                        "drawdown_pct": float(drawdown_pct.iloc[i]),
                        "cooldown_bars": self.cooldown_bars,
                    },
                ))

        logger.info(
            "Portfolio stop-loss mask generated",
            total_bars=len(equity),
            paused_bars=int((~mask).sum()),
            trigger_count=len(self.events),
        )

        return mask


class ConsecutiveLossGuard:
    """Protection against consecutive trading losses.

    After max_consecutive_losses in a row, pause new entries for pause_bars.

    Usage:
        guard = ConsecutiveLossGuard(max_consecutive_losses=5, pause_bars=24)
        mask = guard.generate_mask(trade_pnl_series, index)
        filtered_entries = entries & mask
    """

    def __init__(
        self,
        max_consecutive_losses: int = 5,
        pause_bars: int = 24,
    ) -> None:
        """Initialize consecutive loss guard.

        Args:
            max_consecutive_losses: Number of losses in a row to trigger pause.
            pause_bars: Number of bars to pause after trigger.
        """
        self.max_consecutive_losses = max_consecutive_losses
        self.pause_bars = pause_bars
        self.events: list[RiskEvent] = []

    def generate_mask(
        self,
        trade_pnl: pd.Series,
        full_index: pd.DatetimeIndex,
    ) -> pd.Series:
        """Generate a boolean mask from trade PnL series.

        Args:
            trade_pnl: Series of trade PnL values (indexed by exit timestamp).
            full_index: Full bar index to generate mask for.

        Returns:
            Boolean Series (True = trading allowed).
        """
        mask = pd.Series(True, index=full_index)

        if trade_pnl.empty:
            return mask

        consecutive_losses = 0

        sorted_pnl = trade_pnl.sort_index()

        for exit_time, pnl in sorted_pnl.items():
            if pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            if consecutive_losses >= self.max_consecutive_losses:
                bar_positions = full_index.get_indexer([exit_time], method="nearest")
                if len(bar_positions) > 0 and bar_positions[0] >= 0:
                    start_idx = bar_positions[0]
                    end_idx = min(start_idx + self.pause_bars, len(full_index))
                    mask.iloc[start_idx:end_idx] = False
                    self.events.append(RiskEvent(
                        timestamp=exit_time,
                        event_type="consecutive_loss",
                        details={
                            "consecutive_losses": consecutive_losses,
                            "pause_bars": self.pause_bars,
                        },
                    ))
                consecutive_losses = 0

        logger.info(
            "Consecutive loss guard mask generated",
            total_bars=len(full_index),
            paused_bars=int((~mask).sum()),
            trigger_count=len(self.events),
        )

        return mask


class VolatilityLeverageAdapter:
    """Adaptive leverage based on ATR percentile.

    High volatility → lower leverage, Low volatility → higher leverage.

    Usage:
        adapter = VolatilityLeverageAdapter(max_leverage=3, min_leverage=1)
        leverage_series = adapter.compute(ohlcv)
    """

    def __init__(
        self,
        max_leverage: int = 3,
        min_leverage: int = 1,
        atr_period: int = 14,
        lookback: int = 100,
        high_vol_percentile: float = 80.0,
        low_vol_percentile: float = 20.0,
    ) -> None:
        """Initialize volatility-adaptive leverage.

        Args:
            max_leverage: Maximum leverage (used in low volatility).
            min_leverage: Minimum leverage (used in high volatility).
            atr_period: ATR calculation period.
            lookback: Rolling window for percentile.
            high_vol_percentile: ATR percentile above which leverage is reduced.
            low_vol_percentile: ATR percentile below which max leverage is used.
        """
        self.max_leverage = max_leverage
        self.min_leverage = min_leverage
        self.atr_period = atr_period
        self.lookback = lookback
        self.high_vol_percentile = high_vol_percentile
        self.low_vol_percentile = low_vol_percentile

    def compute(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Compute adaptive leverage series.

        Args:
            ohlcv: DataFrame with high, low, close columns.

        Returns:
            Series of leverage values per bar.
        """
        from backtest_tool.strategies.indicators.atr import compute_atr

        atr = compute_atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], self.atr_period)

        rolling_rank = atr.rolling(self.lookback).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
        )

        leverage = pd.Series(float(self.max_leverage), index=ohlcv.index)

        high_vol = rolling_rank >= self.high_vol_percentile
        leverage[high_vol] = float(self.min_leverage)

        mid_vol = (rolling_rank > self.low_vol_percentile) & (rolling_rank < self.high_vol_percentile)
        if mid_vol.any():
            pct_range = self.high_vol_percentile - self.low_vol_percentile
            mid_pct = (rolling_rank[mid_vol] - self.low_vol_percentile) / pct_range
            leverage[mid_vol] = self.max_leverage - mid_pct * (self.max_leverage - self.min_leverage)

        leverage = leverage.fillna(float(self.max_leverage))

        logger.info(
            "Volatility-adaptive leverage computed",
            mean_leverage=f"{leverage.mean():.2f}",
            min_leverage=f"{leverage.min():.2f}",
            max_leverage=f"{leverage.max():.2f}",
        )

        return leverage
