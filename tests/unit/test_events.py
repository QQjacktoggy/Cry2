"""Tests for core event types."""

from datetime import UTC, datetime

from bot.core.constants import EventType, OrderSide
from bot.core.events import (
    FillEvent,
    KillSwitchEvent,
    MarketEvent,
    SignalEvent,
)


class TestMarketEvent:
    def test_create(self):
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        assert event.symbol == "BTCUSDT"
        assert event.event_type == EventType.MARKET
        assert event.close == 42200.0

    def test_frozen(self):
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42500.0,
            low=41800.0,
            close=42200.0,
            volume=1000.0,
        )
        # Events are frozen (immutable)
        try:
            event.close = 99999.0
            raise AssertionError("Should have raised")
        except Exception:
            pass


class TestSignalEvent:
    def test_create(self):
        signal = SignalEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.1,
        )
        assert signal.event_type == EventType.SIGNAL
        assert signal.side == OrderSide.BUY
        assert signal.quantity == 0.1


class TestFillEvent:
    def test_create(self):
        fill = FillEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.1,
            price=42000.0,
            commission=1.68,
        )
        assert fill.commission == 1.68
        assert fill.realized_pnl == 0.0


class TestKillSwitchEvent:
    def test_create(self):
        event = KillSwitchEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            reason="test",
            triggered_by="user",
        )
        assert event.close_all is True
        assert event.triggered_by == "user"
