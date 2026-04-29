"""Simple publish/subscribe event bus."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


class EventBus:
    """In-process event bus for decoupled component communication."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Any) -> None:
        event_type = type(event).__name__
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.error(
                    "event_handler_error",
                    event_type=event_type,
                    handler=handler.__name__,
                    error=str(exc),
                )

    def publish_status_report(self) -> None:
        """Dedicated trigger for daily summary reports."""
        handlers = self._subscribers.get("status_report", [])
        for handler in handlers:
            handler()
