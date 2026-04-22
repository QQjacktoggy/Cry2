"""Base strategy abstract class.

All strategies implement this interface.
Strategies are event-driven: on_bar for new candles, on_fill for executions,
on_funding for funding rate settlements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import Any

import structlog

from bot.core.constants import OrderSide, OrderType, PositionSide
from bot.core.events import FillEvent, FundingEvent, MarketEvent, SignalEvent
from bot.core.types import Position

logger = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    name: str = "base"
    symbols: list[str] = []
    timeframe: str = "4h"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        """Initialize strategy with YAML parameters.

        Args:
            params: Strategy parameters from config.
        """
        params = params or {}
        self.symbols = params.get("symbols", self.symbols)
        self.timeframe = params.get("timeframe", self.timeframe)
        self.leverage = params.get("leverage", 1)
        self.enabled = params.get("enabled", True)
        self.params = params

        # Internal state
        self._positions: dict[str, Position] = {}
        self._bar_history: dict[str, deque[MarketEvent]] = {}
        self._equity: float = 0.0  # Updated by engine each bar

    @abstractmethod
    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Called when a new K-line arrives.

        Must return list of SignalEvents (empty if no action).
        Signals are generated at bar close, executed at next bar open.
        """

    def set_equity(self, equity: float) -> None:
        """Update current portfolio equity (called by engine each bar)."""
        self._equity = equity

    def on_fill(self, event: FillEvent) -> None:
        """Called when an order is filled."""
        symbol = event.symbol
        pos = self._positions.get(symbol, Position(symbol=symbol, strategy_name=self.name))

        if event.side == OrderSide.BUY:
            if pos.side == PositionSide.SHORT:
                close_qty = min(event.quantity, pos.quantity)
                remaining = pos.quantity - close_qty
                if remaining > 0:
                    pos.quantity = remaining
                else:
                    pos.side = PositionSide.LONG
                    pos.quantity = event.quantity - close_qty
                    pos.entry_price = event.price
            else:
                if pos.quantity > 0:
                    total = pos.entry_price * pos.quantity + event.price * event.quantity
                    pos.quantity += event.quantity
                    pos.entry_price = total / pos.quantity
                else:
                    pos.quantity = event.quantity
                    pos.entry_price = event.price
                pos.side = PositionSide.LONG
        else:
            if pos.side == PositionSide.LONG:
                close_qty = min(event.quantity, pos.quantity)
                remaining = pos.quantity - close_qty
                if remaining > 0:
                    pos.quantity = remaining
                else:
                    pos.side = PositionSide.SHORT
                    pos.quantity = event.quantity - close_qty
                    pos.entry_price = event.price
            else:
                if pos.quantity > 0:
                    total = pos.entry_price * pos.quantity + event.price * event.quantity
                    pos.quantity += event.quantity
                    pos.entry_price = total / pos.quantity
                else:
                    pos.quantity = event.quantity
                    pos.entry_price = event.price
                pos.side = PositionSide.SHORT

        if pos.quantity == 0:
            pos.side = PositionSide.FLAT
            pos.entry_price = 0.0

        pos.current_price = event.price
        self._positions[symbol] = pos

    def on_funding(self, event: FundingEvent) -> None:
        """Called on funding rate settlement. Override if needed."""
        return None

    def warmup_bars(self) -> int:
        """Number of bars needed before generating signals."""
        return 0

    def _record_bar(self, event: MarketEvent, max_history: int = 500) -> None:
        """Store bar in history for indicator calculation."""
        if event.symbol not in self._bar_history:
            self._bar_history[event.symbol] = deque(maxlen=max_history)
        self._bar_history[event.symbol].append(event)

    def _get_closes(self, symbol: str) -> list[float]:
        """Get close prices from bar history."""
        if symbol not in self._bar_history:
            return []
        return [bar.close for bar in self._bar_history[symbol]]

    def _get_highs(self, symbol: str) -> list[float]:
        """Get high prices from bar history."""
        if symbol not in self._bar_history:
            return []
        return [bar.high for bar in self._bar_history[symbol]]

    def _get_lows(self, symbol: str) -> list[float]:
        """Get low prices from bar history."""
        if symbol not in self._bar_history:
            return []
        return [bar.low for bar in self._bar_history[symbol]]

    def _get_position(self, symbol: str) -> Position:
        """Get current position for symbol."""
        return self._positions.get(symbol, Position(symbol=symbol, strategy_name=self.name))

    def _create_signal(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        timestamp: Any,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        stop_price: float = 0.0,
        reduce_only: bool = False,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SignalEvent:
        """Helper to create a SignalEvent."""
        return SignalEvent(
            timestamp=timestamp,
            strategy_name=self.name,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            reason=reason,
            metadata=metadata or {},
            source=self.name,
        )
