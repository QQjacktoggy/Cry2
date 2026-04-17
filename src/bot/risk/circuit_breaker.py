"""Circuit breaker - halts trading on extreme market moves.

Triggers when a single K-line has > 5% price change.
Pauses new orders for a configurable cooldown period (default 30 min).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from bot.core.events import MarketEvent

logger = structlog.get_logger(__name__)


class CircuitBreaker:
    """Monitors for extreme price moves and pauses trading."""

    def __init__(
        self,
        bar_change_threshold_pct: float = 5.0,
        cooldown_minutes: int = 30,
    ) -> None:
        """Initialize circuit breaker.

        Args:
            bar_change_threshold_pct: Max single-bar % change before triggering.
            cooldown_minutes: Minutes to pause after trigger.
        """
        self.bar_change_threshold_pct = bar_change_threshold_pct
        self.cooldown_minutes = cooldown_minutes
        self._tripped: bool = False
        self._trip_time: datetime | None = None
        self._trip_reason: str = ""

    @property
    def is_tripped(self) -> bool:
        """Check if circuit breaker is currently tripped."""
        if not self._tripped:
            return False

        # Check if cooldown has expired
        if self._trip_time:
            elapsed = datetime.now(UTC) - self._trip_time
            if elapsed >= timedelta(minutes=self.cooldown_minutes):
                self.reset()
                return False

        return True

    def check_bar(self, event: MarketEvent) -> bool:
        """Check if a market event triggers the circuit breaker.

        Args:
            event: Market event with OHLCV data.

        Returns:
            True if safe (not triggered), False if circuit breaker tripped.
        """
        if event.open <= 0:
            return True

        bar_change_pct = abs((event.close - event.open) / event.open) * 100

        if bar_change_pct >= self.bar_change_threshold_pct:
            self._tripped = True
            self._trip_time = datetime.now(UTC)
            self._trip_reason = (
                f"{event.symbol} bar change {bar_change_pct:.2f}% "
                f"exceeds threshold {self.bar_change_threshold_pct}%"
            )
            logger.error(
                "circuit_breaker_tripped",
                symbol=event.symbol,
                bar_change_pct=bar_change_pct,
                threshold=self.bar_change_threshold_pct,
                cooldown_min=self.cooldown_minutes,
            )
            return False

        return True

    def reset(self) -> None:
        """Reset the circuit breaker."""
        if self._tripped:
            logger.info("circuit_breaker_reset")
        self._tripped = False
        self._trip_time = None
        self._trip_reason = ""

    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status."""
        remaining_min = 0.0
        if self._tripped and self._trip_time:
            elapsed = (datetime.now(UTC) - self._trip_time).total_seconds() / 60
            remaining_min = max(0, self.cooldown_minutes - elapsed)

        return {
            "tripped": self._tripped,
            "trip_time": self._trip_time.isoformat() if self._trip_time else None,
            "trip_reason": self._trip_reason,
            "remaining_cooldown_min": round(remaining_min, 1),
        }
