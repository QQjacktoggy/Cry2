"""Single position tracking and P&L calculation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from bot.core.constants import OrderSide, PositionSide
from bot.core.types import Position

logger = structlog.get_logger(__name__)


class PositionTracker:
    """Tracks individual positions per strategy and symbol."""

    def __init__(self) -> None:
        self._positions: dict[tuple[str, str], Position] = {}

    def update_on_fill(
        self,
        strategy_name: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
    ) -> float:
        """Update position based on a fill.

        Args:
            strategy_name: Strategy that owns this position.
            symbol: Trading pair.
            side: BUY or SELL.
            quantity: Fill quantity.
            price: Fill price.

        Returns:
            Realized PnL from this fill.
        """
        key = (strategy_name, symbol)
        pos = self._positions.get(key, Position(
            symbol=symbol,
            strategy_name=strategy_name,
        ))

        realized_pnl = 0.0
        now = datetime.now(timezone.utc)

        if side == OrderSide.BUY:
            if pos.side == PositionSide.SHORT and pos.quantity > 0:
                # Closing short
                close_qty = min(quantity, pos.quantity)
                realized_pnl = close_qty * (pos.entry_price - price)
                remaining = pos.quantity - close_qty
                new_qty = quantity - close_qty

                if remaining > 0:
                    pos.quantity = remaining
                elif new_qty > 0:
                    pos.side = PositionSide.LONG
                    pos.quantity = new_qty
                    pos.entry_price = price
                    pos.opened_at = now
                else:
                    pos.side = PositionSide.FLAT
                    pos.quantity = 0.0
                    pos.entry_price = 0.0
            else:
                # Opening/adding long
                if pos.quantity > 0 and pos.side == PositionSide.LONG:
                    total_cost = pos.entry_price * pos.quantity + price * quantity
                    pos.quantity += quantity
                    pos.entry_price = total_cost / pos.quantity
                else:
                    pos.quantity = quantity
                    pos.entry_price = price
                    pos.opened_at = now
                pos.side = PositionSide.LONG
        else:  # SELL
            if pos.side == PositionSide.LONG and pos.quantity > 0:
                # Closing long
                close_qty = min(quantity, pos.quantity)
                realized_pnl = close_qty * (price - pos.entry_price)
                remaining = pos.quantity - close_qty
                new_qty = quantity - close_qty

                if remaining > 0:
                    pos.quantity = remaining
                elif new_qty > 0:
                    pos.side = PositionSide.SHORT
                    pos.quantity = new_qty
                    pos.entry_price = price
                    pos.opened_at = now
                else:
                    pos.side = PositionSide.FLAT
                    pos.quantity = 0.0
                    pos.entry_price = 0.0
            else:
                # Opening/adding short
                if pos.quantity > 0 and pos.side == PositionSide.SHORT:
                    total_cost = pos.entry_price * pos.quantity + price * quantity
                    pos.quantity += quantity
                    pos.entry_price = total_cost / pos.quantity
                else:
                    pos.quantity = quantity
                    pos.entry_price = price
                    pos.opened_at = now
                pos.side = PositionSide.SHORT

        pos.current_price = price
        pos.realized_pnl += realized_pnl
        pos.updated_at = now
        self._positions[key] = pos

        return realized_pnl

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price for all positions of a symbol."""
        for (strat, sym), pos in self._positions.items():
            if sym == symbol:
                pos.current_price = price
                if pos.is_open:
                    if pos.side == PositionSide.LONG:
                        pos.unrealized_pnl = pos.quantity * (price - pos.entry_price)
                    elif pos.side == PositionSide.SHORT:
                        pos.unrealized_pnl = pos.quantity * (pos.entry_price - price)

    def get_position(self, strategy_name: str, symbol: str) -> Position:
        """Get a specific position."""
        key = (strategy_name, symbol)
        return self._positions.get(key, Position(symbol=symbol, strategy_name=strategy_name))

    def get_all_positions(self) -> list[Position]:
        """Get all positions."""
        return list(self._positions.values())

    def get_open_positions(self) -> list[Position]:
        """Get all open positions."""
        return [p for p in self._positions.values() if p.is_open]

    def get_strategy_positions(self, strategy_name: str) -> list[Position]:
        """Get all positions for a specific strategy."""
        return [
            p for (strat, _), p in self._positions.items()
            if strat == strategy_name
        ]

    def total_unrealized_pnl(self) -> float:
        """Calculate total unrealized PnL across all positions."""
        return sum(p.unrealized_pnl for p in self._positions.values() if p.is_open)

    def total_realized_pnl(self) -> float:
        """Calculate total realized PnL across all positions."""
        return sum(p.realized_pnl for p in self._positions.values())
