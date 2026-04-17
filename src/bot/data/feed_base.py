"""Abstract Data Feed interface shared by backtest and live feeds."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generator

from bot.core.events import MarketEvent, FundingEvent
from bot.core.event_bus import EventBus
from bot.core.clock import BaseClock


class DataFeed(ABC):
    """Abstract base class for data feeds.

    Both backtest and live feeds implement this interface,
    ensuring strategy code works identically in both modes.
    """

    def __init__(self, event_bus: EventBus, clock: BaseClock) -> None:
        self.event_bus = event_bus
        self.clock = clock
        self._running = False

    @abstractmethod
    def start(self) -> None:
        """Start the data feed."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the data feed."""

    @abstractmethod
    def has_next(self) -> bool:
        """Check if more data is available (backtest mode)."""

    @abstractmethod
    def next(self) -> MarketEvent | FundingEvent | None:
        """Get next event (backtest mode)."""

    @property
    def is_running(self) -> bool:
        """Check if the feed is active."""
        return self._running
