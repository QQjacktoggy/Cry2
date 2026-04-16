"""Kill Switch - emergency stop mechanism.

Triggers:
- Manual activation (Telegram command / CLI)
- Max consecutive errors reached
- WebSocket disconnected > timeout
- API latency too high

Actions:
- Close all positions with market orders
- Cancel all pending orders
- Halt all trading
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from bot.core.constants import EventType, OrderSide, OrderType, PositionSide
from bot.core.event_bus import EventBus
from bot.core.events import KillSwitchEvent, OrderEvent
from bot.core.exceptions import KillSwitchActivated
from bot.core.types import Position

logger = structlog.get_logger(__name__)


class KillSwitchState(str, Enum):
    """Kill switch states."""
    ARMED = "armed"       # Normal - monitoring
    TRIGGERED = "triggered"  # Activated - closing positions
    RESOLVED = "resolved"    # Manually resolved


class KillSwitch:
    """Emergency stop mechanism."""

    def __init__(
        self,
        event_bus: EventBus,
        max_consecutive_errors: int = 5,
        max_api_latency_ms: int = 3000,
    ) -> None:
        self.event_bus = event_bus
        self.max_consecutive_errors = max_consecutive_errors
        self.max_api_latency_ms = max_api_latency_ms

        self._state = KillSwitchState.ARMED
        self._consecutive_errors: int = 0
        self._trigger_reason: str = ""
        self._triggered_at: datetime | None = None

    @property
    def state(self) -> KillSwitchState:
        """Get current state."""
        return self._state

    @property
    def is_triggered(self) -> bool:
        """Check if kill switch is active."""
        return self._state == KillSwitchState.TRIGGERED

    def trigger(self, reason: str, triggered_by: str = "system") -> None:
        """Activate the kill switch.

        Args:
            reason: Why the kill switch was triggered.
            triggered_by: Who triggered it (system/user/telegram).
        """
        if self._state == KillSwitchState.TRIGGERED:
            logger.warning("kill_switch_already_triggered")
            return

        self._state = KillSwitchState.TRIGGERED
        self._trigger_reason = reason
        self._triggered_at = datetime.now(timezone.utc)

        logger.critical(
            "kill_switch_triggered",
            reason=reason,
            triggered_by=triggered_by,
        )

        # Publish kill switch event
        event = KillSwitchEvent(
            timestamp=self._triggered_at,
            reason=reason,
            triggered_by=triggered_by,
            close_all=True,
            source="kill_switch",
        )
        self.event_bus.publish(event)

    def close_all_positions(self, positions: list[Position]) -> None:
        """Send market orders to close all open positions."""
        now = datetime.now(timezone.utc)
        for pos in positions:
            if not pos.is_open:
                continue

            side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = OrderEvent(
                timestamp=now,
                strategy_name=pos.strategy_name,
                symbol=pos.symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=pos.quantity,
                reduce_only=True,
                source="kill_switch",
            )
            self.event_bus.publish(order)
            logger.warning(
                "kill_switch_closing",
                symbol=pos.symbol,
                quantity=pos.quantity,
                side=side.value,
            )

    def record_error(self) -> None:
        """Record an error occurrence. Triggers if threshold reached."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_consecutive_errors:
            self.trigger(
                f"Consecutive errors reached {self._consecutive_errors}",
                triggered_by="system",
            )

    def record_success(self) -> None:
        """Record a successful operation (resets error counter)."""
        self._consecutive_errors = 0

    def check_api_latency(self, latency_ms: float) -> None:
        """Check if API latency is within bounds."""
        if latency_ms > self.max_api_latency_ms:
            logger.error("api_latency_high", latency_ms=latency_ms)
            self.record_error()

    def resolve(self) -> None:
        """Manually resolve the kill switch (re-arm)."""
        if self._state != KillSwitchState.TRIGGERED:
            return

        self._state = KillSwitchState.RESOLVED
        self._consecutive_errors = 0
        logger.info("kill_switch_resolved")

    def rearm(self) -> None:
        """Re-arm the kill switch after resolution."""
        self._state = KillSwitchState.ARMED
        self._consecutive_errors = 0
        self._trigger_reason = ""
        self._triggered_at = None
        logger.info("kill_switch_rearmed")

    def get_status(self) -> dict[str, Any]:
        """Get kill switch status."""
        return {
            "state": self._state.value,
            "consecutive_errors": self._consecutive_errors,
            "trigger_reason": self._trigger_reason,
            "triggered_at": self._triggered_at.isoformat() if self._triggered_at else None,
        }
