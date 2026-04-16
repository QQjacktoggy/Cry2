"""Liquidation guard - monitors maintenance margin ratio.

When margin ratio drops below threshold, auto-deleverages positions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from bot.core.constants import EventType, OrderSide, OrderType, PositionSide
from bot.core.event_bus import EventBus
from bot.core.events import LiquidationEvent, OrderEvent, SignalEvent
from bot.core.types import Position

logger = structlog.get_logger(__name__)


class LiquidationGuard:
    """Monitors margin ratio and auto-deleverages when needed."""

    def __init__(
        self,
        event_bus: EventBus,
        margin_ratio_min: float = 50.0,
        reduce_amount_pct: float = 30.0,
    ) -> None:
        """Initialize liquidation guard.

        Args:
            event_bus: Event bus for publishing events.
            margin_ratio_min: Minimum margin ratio % before action.
            reduce_amount_pct: Percentage of position to reduce.
        """
        self.event_bus = event_bus
        self.margin_ratio_min = margin_ratio_min
        self.reduce_amount_pct = reduce_amount_pct

    def check(
        self,
        positions: list[Position],
        total_margin: float,
        maintenance_margin: float,
    ) -> bool:
        """Check margin ratio and auto-deleverage if needed.

        Args:
            positions: All open positions.
            total_margin: Total margin (equity).
            maintenance_margin: Required maintenance margin.

        Returns:
            True if margin is safe, False if deleverage was triggered.
        """
        if maintenance_margin <= 0 or total_margin <= 0:
            return True

        margin_ratio = (total_margin / maintenance_margin) * 100

        if margin_ratio < self.margin_ratio_min:
            logger.error(
                "margin_ratio_low",
                margin_ratio=margin_ratio,
                threshold=self.margin_ratio_min,
                total_margin=total_margin,
                maintenance_margin=maintenance_margin,
            )

            # Publish liquidation event
            event = LiquidationEvent(
                timestamp=datetime.now(timezone.utc),
                symbol="ALL",
                margin_ratio=margin_ratio,
                maintenance_margin=maintenance_margin,
                current_margin=total_margin,
                source="liquidation_guard",
            )
            self.event_bus.publish(event)

            # Auto-deleverage: reduce each position
            self._reduce_positions(positions)
            return False

        return True

    def _reduce_positions(self, positions: list[Position]) -> None:
        """Reduce all open positions by configured percentage."""
        for pos in positions:
            if not pos.is_open:
                continue

            reduce_qty = pos.quantity * (self.reduce_amount_pct / 100.0)
            if reduce_qty <= 0:
                continue

            # Create reduce-only order
            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = OrderEvent(
                timestamp=datetime.now(timezone.utc),
                strategy_name=pos.strategy_name,
                symbol=pos.symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=reduce_qty,
                reduce_only=True,
                source="liquidation_guard",
            )
            self.event_bus.publish(order)
            logger.warning(
                "auto_deleverage",
                symbol=pos.symbol,
                reduce_qty=reduce_qty,
                strategy=pos.strategy_name,
            )
