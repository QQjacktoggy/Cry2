"""Event Bus implementation - pub/sub pattern for inter-module communication.

All modules subscribe to events and publish events through this central bus.
Supports both sync and async handlers.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any, Callable

import structlog

from bot.core.events import BaseEvent

logger = structlog.get_logger(__name__)

EventHandler = Callable[[BaseEvent], Any]


class EventBus:
    """Central event bus for pub/sub communication."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = threading.Lock()
        self._event_count: int = 0

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        with self._lock:
            self._handlers[event_type].append(handler)
            logger.debug("handler_subscribed", event_type=event_type, handler=handler.__name__)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe a handler from an event type."""
        with self._lock:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)
                logger.debug(
                    "handler_unsubscribed", event_type=event_type, handler=handler.__name__
                )

    def publish(self, event: BaseEvent) -> None:
        """Publish an event to all subscribed handlers (synchronous)."""
        event_type = event.event_type.value
        self._event_count += 1

        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    logger.warning(
                        "async_handler_in_sync_publish",
                        handler=handler.__name__,
                        event_type=event_type,
                    )
            except Exception:
                logger.exception(
                    "event_handler_error",
                    handler=handler.__name__,
                    event_type=event_type,
                )

    async def publish_async(self, event: BaseEvent) -> None:
        """Publish an event to all subscribed handlers (async)."""
        event_type = event.event_type.value
        self._event_count += 1

        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "event_handler_error",
                    handler=handler.__name__,
                    event_type=event_type,
                )

    @property
    def event_count(self) -> int:
        """Return total events published."""
        return self._event_count

    def clear(self) -> None:
        """Clear all handlers."""
        with self._lock:
            self._handlers.clear()
            self._event_count = 0
