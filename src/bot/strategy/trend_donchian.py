"""Strategy C: Trend Following (Donchian Breakout).

Entry: 20-period Donchian breakout + ADX > 25
Exit: 10-period Donchian reverse or trailing ATR stop
Position: (equity * 1%) / (2 * ATR) * leverage
"""

from __future__ import annotations

from typing import Any

import structlog

from bot.core.constants import OrderSide, OrderType, PositionSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy
from bot.strategy.indicators.atr import calculate_atr
from bot.strategy.indicators.bollinger import calculate_adx
from bot.strategy.indicators.donchian import calculate_donchian

logger = structlog.get_logger(__name__)


class TrendDonchianStrategy(BaseStrategy):
    """Trend following strategy using Donchian Breakout."""

    name = "trend_donchian"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.entry_period = params.get("entry_period", 20)
        self.exit_period = params.get("exit_period", 10)
        self.adx_period = params.get("adx_period", 14)
        self.adx_threshold = params.get("adx_threshold", 25)
        self.atr_period = params.get("atr_period", 14)
        self.atr_stop_mult = params.get("atr_stop_mult", 2.0)
        self.risk_per_trade_pct = params.get("risk_per_trade_pct", 1.0)

        # Trailing stop state
        self._trailing_stops: dict[str, float] = {}

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Process bar for Donchian breakout signals."""
        self._record_bar(event, max_history=300)
        signals: list[SignalEvent] = []
        symbol = event.symbol

        highs = self._get_highs(symbol)
        lows = self._get_lows(symbol)
        closes = self._get_closes(symbol)

        if len(closes) < self.entry_period + 1:
            return signals

        # Calculate indicators
        entry_upper, entry_lower, _ = calculate_donchian(highs[:-1], lows[:-1], self.entry_period)
        exit_upper, exit_lower, _ = calculate_donchian(highs[:-1], lows[:-1], self.exit_period)
        atr = calculate_atr(highs, lows, closes, self.atr_period)
        adx = calculate_adx(highs, lows, closes, self.adx_period)

        pos = self._get_position(symbol)
        price = event.close

        if pos.is_open:
            # Manage existing position
            signals.extend(
                self._manage_position(symbol, pos, price, atr, exit_upper, exit_lower, event.timestamp)
            )
        else:
            # Look for entries
            if adx >= self.adx_threshold and entry_upper > 0:
                if price > entry_upper:
                    # Breakout up -> Long
                    quantity = self._calc_position_size(price, atr)
                    if quantity > 0:
                        self._trailing_stops[symbol] = price - self.atr_stop_mult * atr
                        signals.append(self._create_signal(
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=quantity,
                            timestamp=event.timestamp,
                            reason=f"Donchian breakout up, ADX={adx:.1f}",
                        ))

                elif price < entry_lower:
                    # Breakout down -> Short
                    quantity = self._calc_position_size(price, atr)
                    if quantity > 0:
                        self._trailing_stops[symbol] = price + self.atr_stop_mult * atr
                        signals.append(self._create_signal(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=quantity,
                            timestamp=event.timestamp,
                            reason=f"Donchian breakout down, ADX={adx:.1f}",
                        ))

        return signals

    def _manage_position(
        self,
        symbol: str,
        pos: Position,
        price: float,
        atr: float,
        exit_upper: float,
        exit_lower: float,
        timestamp: Any,
    ) -> list[SignalEvent]:
        """Manage existing position - trailing stop + Donchian exit."""
        signals: list[SignalEvent] = []

        if pos.side == PositionSide.LONG:
            # Update trailing stop (ratchet up)
            new_stop = price - self.atr_stop_mult * atr
            current_stop = self._trailing_stops.get(symbol, 0.0)
            self._trailing_stops[symbol] = max(current_stop, new_stop)

            # Check stop or Donchian exit
            if price <= self._trailing_stops[symbol] or price < exit_lower:
                reason = "Trailing stop" if price <= self._trailing_stops[symbol] else "Donchian exit"
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason=reason,
                ))
                self._trailing_stops.pop(symbol, None)

        elif pos.side == PositionSide.SHORT:
            # Update trailing stop (ratchet down)
            new_stop = price + self.atr_stop_mult * atr
            current_stop = self._trailing_stops.get(symbol, float("inf"))
            self._trailing_stops[symbol] = min(current_stop, new_stop)

            if price >= self._trailing_stops[symbol] or price > exit_upper:
                reason = "Trailing stop" if price >= self._trailing_stops[symbol] else "Donchian exit"
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason=reason,
                ))
                self._trailing_stops.pop(symbol, None)

        return signals

    def _calc_position_size(self, price: float, atr: float) -> float:
        """Calculate position size based on ATR risk."""
        if atr <= 0 or price <= 0:
            return 0.0
        # Risk = equity * risk_pct / (atr_mult * atr)
        risk_amount = 10000.0 * (self.risk_per_trade_pct / 100.0)  # Simplified
        stop_distance = self.atr_stop_mult * atr
        quantity = (risk_amount / stop_distance) * self.leverage
        return quantity

    def warmup_bars(self) -> int:
        return max(self.entry_period, self.adx_period * 2, self.atr_period) + 5
