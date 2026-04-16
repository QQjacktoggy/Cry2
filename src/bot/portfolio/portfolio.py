"""Portfolio - aggregates all positions and tracks overall account state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from bot.core.constants import EventType, OrderSide
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, MarketEvent
from bot.core.types import AccountSnapshot, Position
from bot.portfolio.position import PositionTracker

logger = structlog.get_logger(__name__)


class Portfolio:
    """Manages overall portfolio state.

    Tracks:
    - Cash balance
    - All positions (per strategy)
    - Equity curve
    - PnL tracking
    """

    def __init__(
        self,
        event_bus: EventBus,
        initial_capital: float = 10000.0,
    ) -> None:
        self.event_bus = event_bus
        self.initial_capital = initial_capital
        self._cash = initial_capital
        self._tracker = PositionTracker()
        self._equity_history: list[tuple[datetime, float]] = []
        self._total_fees: float = 0.0
        self._total_funding: float = 0.0

        # Subscribe to events
        self.event_bus.subscribe(EventType.FILL.value, self._on_fill)
        self.event_bus.subscribe(EventType.MARKET.value, self._on_market)

    def _on_fill(self, event: FillEvent) -> None:
        """Handle fill event - update positions and cash."""
        realized_pnl = self._tracker.update_on_fill(
            strategy_name=event.strategy_name,
            symbol=event.symbol,
            side=event.side,
            quantity=event.quantity,
            price=event.price,
        )

        self._cash += realized_pnl
        self._cash -= event.commission
        self._total_fees += event.commission

        logger.debug(
            "portfolio_fill",
            symbol=event.symbol,
            pnl=realized_pnl,
            fee=event.commission,
            cash=self._cash,
        )

    def _on_market(self, event: MarketEvent) -> None:
        """Handle market event - update position prices."""
        self._tracker.update_price(event.symbol, event.close)

    def record_funding_payment(self, amount: float) -> None:
        """Record a funding rate payment."""
        self._cash += amount
        self._total_funding += amount

    def record_equity(self, timestamp: datetime | None = None) -> None:
        """Record current equity for equity curve tracking."""
        ts = timestamp or datetime.now(timezone.utc)
        self._equity_history.append((ts, self.equity))

    @property
    def cash(self) -> float:
        """Available cash balance."""
        return self._cash

    @property
    def equity(self) -> float:
        """Total equity (cash + unrealized PnL)."""
        return self._cash + self._tracker.total_unrealized_pnl()

    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized PnL."""
        return self._tracker.total_unrealized_pnl()

    @property
    def realized_pnl(self) -> float:
        """Total realized PnL."""
        return self._tracker.total_realized_pnl()

    @property
    def total_return(self) -> float:
        """Total return as a decimal (e.g., 0.15 = 15%)."""
        if self.initial_capital <= 0:
            return 0.0
        return (self.equity - self.initial_capital) / self.initial_capital

    def get_positions(self) -> list[Position]:
        """Get all positions."""
        return self._tracker.get_all_positions()

    def get_open_positions(self) -> list[Position]:
        """Get all open positions."""
        return self._tracker.get_open_positions()

    def get_strategy_positions(self, strategy_name: str) -> list[Position]:
        """Get positions for a specific strategy."""
        return self._tracker.get_strategy_positions(strategy_name)

    def get_equity_history(self) -> list[tuple[datetime, float]]:
        """Get equity curve history."""
        return list(self._equity_history)

    def take_snapshot(self) -> AccountSnapshot:
        """Create an account snapshot."""
        return AccountSnapshot(
            timestamp=datetime.now(timezone.utc),
            total_equity=self.equity,
            available_balance=self._cash,
            total_unrealized_pnl=self.unrealized_pnl,
            total_realized_pnl=self.realized_pnl,
            positions=self.get_open_positions(),
            metadata={
                "total_fees": self._total_fees,
                "total_funding": self._total_funding,
                "initial_capital": self.initial_capital,
            },
        )
