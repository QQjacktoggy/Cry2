"""Central risk manager - checks all risk rules before allowing orders.

Multi-layer risk checks:
- Per-trade risk limit
- Daily loss limit → halt for day
- Weekly loss limit → halt for week
- Leverage limit
- Position value limit
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

from bot.core.constants import EventType, OrderSide
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, OrderEvent, RejectEvent, SignalEvent
from bot.core.exceptions import RiskLimitExceeded
from bot.core.types import Position

logger = structlog.get_logger(__name__)


class RiskManager:
    """Central risk manager that gates all orders."""

    def __init__(
        self,
        event_bus: EventBus,
        max_risk_per_trade_pct: float = 1.0,
        max_position_value_pct: float = 20.0,
        max_leverage: int = 3,
        daily_loss_limit_pct: float = 3.0,
        weekly_loss_limit_pct: float = 8.0,
        daily_trade_count_limit: int = 50,
    ) -> None:
        self.event_bus = event_bus
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_position_value_pct = max_position_value_pct
        self.max_leverage = max_leverage
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.weekly_loss_limit_pct = weekly_loss_limit_pct
        self.daily_trade_count_limit = daily_trade_count_limit

        # State tracking
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._daily_halted: bool = False
        self._weekly_halted: bool = False
        self._last_daily_reset: datetime = datetime.now(timezone.utc)
        self._last_weekly_reset: datetime = datetime.now(timezone.utc)
        self._equity: float = 0.0

        # Subscribe to events
        self.event_bus.subscribe(EventType.SIGNAL.value, self._on_signal)
        self.event_bus.subscribe(EventType.FILL.value, self._on_fill)

    def set_equity(self, equity: float) -> None:
        """Update current equity for risk calculations."""
        self._equity = equity

    def _check_daily_reset(self) -> None:
        """Reset daily counters at UTC midnight."""
        now = datetime.now(timezone.utc)
        if now.date() > self._last_daily_reset.date():
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._daily_halted = False
            self._last_daily_reset = now
            logger.info("daily_risk_reset")

    def _check_weekly_reset(self) -> None:
        """Reset weekly counters on Monday."""
        now = datetime.now(timezone.utc)
        days_since_reset = (now - self._last_weekly_reset).days
        if days_since_reset >= 7:
            self._weekly_pnl = 0.0
            self._weekly_halted = False
            self._last_weekly_reset = now
            logger.info("weekly_risk_reset")

    def check_signal(self, signal: SignalEvent, equity: float) -> tuple[bool, str]:
        """Check if a signal passes all risk checks.

        Args:
            signal: The trading signal to check.
            equity: Current account equity.

        Returns:
            (passed, reason) tuple.
        """
        self._check_daily_reset()
        self._check_weekly_reset()
        self._equity = equity

        # Check halted state
        if self._daily_halted:
            return False, "Daily loss limit reached - trading halted for today"

        if self._weekly_halted:
            return False, "Weekly loss limit reached - trading halted for this week"

        # Check daily trade count
        if self._daily_trade_count >= self.daily_trade_count_limit:
            return False, f"Daily trade count limit reached ({self.daily_trade_count_limit})"

        # Check position value limit
        if signal.quantity > 0 and signal.price > 0:
            notional = signal.quantity * signal.price
            max_notional = equity * (self.max_position_value_pct / 100.0)
            if notional > max_notional:
                return False, (
                    f"Position value {notional:.2f} exceeds limit "
                    f"{max_notional:.2f} ({self.max_position_value_pct}% of equity)"
                )

        return True, ""

    def _on_signal(self, event: SignalEvent) -> None:
        """Handle incoming signal - check risk and convert to order."""
        passed, reason = self.check_signal(event, self._equity)

        if not passed:
            reject = RejectEvent(
                timestamp=datetime.now(timezone.utc),
                strategy_name=event.strategy_name,
                symbol=event.symbol,
                reason=reason,
                source="risk_manager",
            )
            self.event_bus.publish(reject)
            logger.warning(
                "signal_rejected",
                strategy=event.strategy_name,
                symbol=event.symbol,
                reason=reason,
            )
            return

        # Convert signal to order
        order = OrderEvent(
            timestamp=event.timestamp,
            strategy_name=event.strategy_name,
            symbol=event.symbol,
            side=event.side,
            order_type=event.order_type,
            quantity=event.quantity,
            price=event.price,
            stop_price=event.stop_price,
            reduce_only=event.reduce_only,
            post_only=event.post_only,
            client_order_id="",
            source="risk_manager",
        )
        self.event_bus.publish(order)

    def _on_fill(self, event: FillEvent) -> None:
        """Track PnL from fills for daily/weekly limits."""
        self._daily_pnl += event.realized_pnl
        self._weekly_pnl += event.realized_pnl
        self._daily_trade_count += 1

        # Check daily loss limit
        if self._equity > 0:
            daily_loss_pct = abs(min(self._daily_pnl, 0)) / self._equity * 100
            if daily_loss_pct >= self.daily_loss_limit_pct and not self._daily_halted:
                self._daily_halted = True
                logger.error(
                    "daily_loss_limit_hit",
                    daily_pnl=self._daily_pnl,
                    limit_pct=self.daily_loss_limit_pct,
                )

            weekly_loss_pct = abs(min(self._weekly_pnl, 0)) / self._equity * 100
            if weekly_loss_pct >= self.weekly_loss_limit_pct and not self._weekly_halted:
                self._weekly_halted = True
                logger.error(
                    "weekly_loss_limit_hit",
                    weekly_pnl=self._weekly_pnl,
                    limit_pct=self.weekly_loss_limit_pct,
                )

    @property
    def is_halted(self) -> bool:
        """Check if trading is halted."""
        return self._daily_halted or self._weekly_halted

    @property
    def daily_pnl(self) -> float:
        """Get current daily PnL."""
        return self._daily_pnl

    @property
    def weekly_pnl(self) -> float:
        """Get current weekly PnL."""
        return self._weekly_pnl

    def get_status(self) -> dict[str, Any]:
        """Get risk manager status summary."""
        return {
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "daily_trade_count": self._daily_trade_count,
            "daily_halted": self._daily_halted,
            "weekly_halted": self._weekly_halted,
            "equity": self._equity,
        }
