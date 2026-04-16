"""Unified time source - SimClock for backtest, RealClock for live.

All modules MUST use the clock interface to get current time, ensuring
backtest and live code behave identically.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class BaseClock(ABC):
    """Abstract clock interface."""

    @abstractmethod
    def now(self) -> datetime:
        """Return current datetime (UTC)."""

    @abstractmethod
    def now_ms(self) -> int:
        """Return current time as Unix milliseconds."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep for specified duration."""


class RealClock(BaseClock):
    """Real-time clock for live/paper trading."""

    def now(self) -> datetime:
        """Return current UTC datetime."""
        return datetime.now(timezone.utc)

    def now_ms(self) -> int:
        """Return current time as Unix milliseconds."""
        return int(time.time() * 1000)

    def sleep(self, seconds: float) -> None:
        """Sleep for specified duration."""
        time.sleep(seconds)


class SimClock(BaseClock):
    """Simulated clock for backtesting.

    Time advances only when explicitly set, ensuring deterministic behavior.
    """

    def __init__(self, start_ms: int = 0) -> None:
        self._current_ms: int = start_ms

    def now(self) -> datetime:
        """Return simulated UTC datetime."""
        return datetime.fromtimestamp(self._current_ms / 1000, tz=timezone.utc)

    def now_ms(self) -> int:
        """Return simulated time as Unix milliseconds."""
        return self._current_ms

    def sleep(self, seconds: float) -> None:
        """Advance simulated time (no actual sleep)."""
        self._current_ms += int(seconds * 1000)

    def set_time(self, timestamp_ms: int) -> None:
        """Set the simulated time."""
        self._current_ms = timestamp_ms

    def advance(self, milliseconds: int) -> None:
        """Advance time by specified milliseconds."""
        self._current_ms += milliseconds
