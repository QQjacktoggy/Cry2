"""Tests for the event bus."""

from datetime import datetime, timezone

from bot.core.constants import EventType
from bot.core.events import MarketEvent


class TestEventBus:
    def test_subscribe_and_publish(self, event_bus):
        received = []

        def handler(event):
            received.append(event)

        event_bus.subscribe(EventType.MARKET.value, handler)

        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        event_bus.publish(event)

        assert len(received) == 1
        assert received[0].symbol == "BTCUSDT"

    def test_multiple_handlers(self, event_bus):
        count = {"a": 0, "b": 0}

        def handler_a(event):
            count["a"] += 1

        def handler_b(event):
            count["b"] += 1

        event_bus.subscribe(EventType.MARKET.value, handler_a)
        event_bus.subscribe(EventType.MARKET.value, handler_b)

        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        event_bus.publish(event)

        assert count["a"] == 1
        assert count["b"] == 1

    def test_unsubscribe(self, event_bus):
        received = []

        def handler(event):
            received.append(event)

        event_bus.subscribe(EventType.MARKET.value, handler)
        event_bus.unsubscribe(EventType.MARKET.value, handler)

        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        event_bus.publish(event)

        assert len(received) == 0

    def test_handler_exception_isolated(self, event_bus):
        """One handler failing shouldn't stop others."""
        received = []

        def bad_handler(event):
            raise ValueError("boom")

        def good_handler(event):
            received.append(event)

        event_bus.subscribe(EventType.MARKET.value, bad_handler)
        event_bus.subscribe(EventType.MARKET.value, good_handler)

        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        event_bus.publish(event)

        assert len(received) == 1

    def test_event_count(self, event_bus):
        event_bus.subscribe(EventType.MARKET.value, lambda e: None)
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        event_bus.publish(event)
        event_bus.publish(event)
        assert event_bus.event_count == 2
