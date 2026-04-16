"""Strategy B: Futures Grid with Trend Filter.

Grid trading in a 20-day range with EMA200 trend filter.
Bullish: only long grids. Bearish: only short grids. Ranging: both.
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from bot.core.constants import OrderSide, OrderType, PositionSide
from bot.core.events import FillEvent, MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy
from bot.strategy.indicators.atr import calculate_atr
from bot.strategy.indicators.bollinger import calculate_ema

logger = structlog.get_logger(__name__)


class GridFuturesStrategy(BaseStrategy):
    """Grid trading strategy for futures with trend filter."""

    name = "grid_futures"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.grid_count = params.get("grid_count", 20)
        self.grid_spacing = params.get("grid_spacing", "geometric")
        self.per_grid_size_pct = params.get("per_grid_size_pct", 1.0)
        self.stop_loss_atr_mult = params.get("stop_loss_atr_mult", 1.0)

        trend_filter = params.get("trend_filter", {})
        self.trend_filter_enabled = trend_filter.get("enabled", True)
        self.ema_period = trend_filter.get("ema_period", 200)

        # Grid state per symbol
        self._grid_levels: dict[str, list[float]] = {}
        self._filled_levels: dict[str, set[int]] = {}
        self._upper: dict[str, float] = {}
        self._lower: dict[str, float] = {}

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Process bar and manage grid."""
        self._record_bar(event, max_history=250)
        signals: list[SignalEvent] = []
        symbol = event.symbol

        closes = self._get_closes(symbol)
        highs = self._get_highs(symbol)
        lows = self._get_lows(symbol)

        if len(closes) < max(self.ema_period, 20):
            return signals

        # Calculate grid bounds (20-day range)
        upper = max(highs[-20:]) * 1.02
        lower = min(lows[-20:]) * 0.98

        # Check if grid needs reset
        if symbol not in self._grid_levels or self._needs_reset(symbol, event.close, closes, highs, lows):
            self._setup_grid(symbol, upper, lower)
            self._upper[symbol] = upper
            self._lower[symbol] = lower

        # Determine trend
        trend = self._get_trend(closes)

        # Check grid levels
        grid = self._grid_levels.get(symbol, [])
        filled = self._filled_levels.get(symbol, set())

        for i, level in enumerate(grid):
            if i in filled:
                continue

            if event.low <= level <= event.high:
                # Price touched this grid level
                is_lower_half = level < (upper + lower) / 2

                if trend == "bullish" and is_lower_half:
                    signals.append(self._create_grid_signal(
                        symbol, OrderSide.BUY, level, event.timestamp
                    ))
                    filled.add(i)
                elif trend == "bearish" and not is_lower_half:
                    signals.append(self._create_grid_signal(
                        symbol, OrderSide.SELL, level, event.timestamp
                    ))
                    filled.add(i)
                elif trend == "ranging":
                    side = OrderSide.BUY if is_lower_half else OrderSide.SELL
                    signals.append(self._create_grid_signal(
                        symbol, side, level, event.timestamp
                    ))
                    filled.add(i)

        self._filled_levels[symbol] = filled
        return signals

    def _setup_grid(self, symbol: str, upper: float, lower: float) -> None:
        """Set up grid levels between upper and lower bounds."""
        levels = []
        if self.grid_spacing == "geometric" and lower > 0 and upper > lower:
            ratio = (upper / lower) ** (1.0 / self.grid_count)
            for i in range(self.grid_count + 1):
                levels.append(lower * (ratio ** i))
        else:
            step = (upper - lower) / self.grid_count
            for i in range(self.grid_count + 1):
                levels.append(lower + step * i)

        self._grid_levels[symbol] = levels
        self._filled_levels[symbol] = set()

    def _needs_reset(self, symbol: str, price: float, closes: list[float], highs: list[float], lows: list[float]) -> bool:
        """Check if grid needs to be reset (price broke out)."""
        if symbol not in self._upper:
            return True

        atr = calculate_atr(highs, lows, closes, 14)
        upper = self._upper[symbol]
        lower = self._lower[symbol]

        if price > upper + atr * self.stop_loss_atr_mult:
            return True
        if price < lower - atr * self.stop_loss_atr_mult:
            return True

        return False

    def _get_trend(self, closes: list[float]) -> str:
        """Determine trend using EMA."""
        if not self.trend_filter_enabled:
            return "ranging"

        ema = calculate_ema(closes, self.ema_period)
        if ema == 0:
            return "ranging"

        current = closes[-1]
        if current > ema * 1.01:
            return "bullish"
        elif current < ema * 0.99:
            return "bearish"
        return "ranging"

    def _create_grid_signal(
        self, symbol: str, side: OrderSide, price: float, timestamp: Any
    ) -> SignalEvent:
        """Create a grid order signal."""
        # Simplified quantity
        quantity = 100.0 / price  # ~$100 per grid
        return self._create_signal(
            symbol=symbol,
            side=side,
            quantity=quantity,
            timestamp=timestamp,
            reason=f"Grid level at {price:.2f}",
        )

    def warmup_bars(self) -> int:
        return max(self.ema_period, 20) + 1
