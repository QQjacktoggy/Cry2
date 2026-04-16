"""Integration test: Strategy → Risk → Execution event flow."""

from datetime import datetime, timezone

from bot.core.constants import EventType, OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, MarketEvent, OrderEvent, SignalEvent


class TestEventFlow:
    def test_signal_to_order_to_fill(self):
        """Test full event chain: signal → order → fill."""
        event_bus = EventBus()
        orders = []
        fills = []

        # Simulate risk manager (pass-through)
        def risk_check(signal):
            order = OrderEvent(
                timestamp=signal.timestamp,
                strategy_name=signal.strategy_name,
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                quantity=signal.quantity,
                source="risk",
            )
            orders.append(order)
            event_bus.publish(order)

        # Simulate executor
        def executor(order):
            fill = FillEvent(
                timestamp=order.timestamp,
                strategy_name=order.strategy_name,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=42000.0,
                commission=1.68,
                source="sim",
            )
            fills.append(fill)
            event_bus.publish(fill)

        event_bus.subscribe(EventType.SIGNAL.value, risk_check)
        event_bus.subscribe(EventType.ORDER.value, executor)

        # Publish signal
        signal = SignalEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.1,
            source="test",
        )
        event_bus.publish(signal)

        assert len(orders) == 1
        assert len(fills) == 1
        assert fills[0].price == 42000.0
        assert fills[0].commission == 1.68

    def test_multiple_strategies(self):
        """Test multiple strategies publishing signals."""
        event_bus = EventBus()
        all_signals = []

        event_bus.subscribe(EventType.SIGNAL.value, lambda e: all_signals.append(e))

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for strategy in ["strategy_a", "strategy_b", "strategy_c"]:
            signal = SignalEvent(
                timestamp=ts,
                strategy_name=strategy,
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                quantity=0.1,
                source=strategy,
            )
            event_bus.publish(signal)

        assert len(all_signals) == 3
        strategies = {s.strategy_name for s in all_signals}
        assert strategies == {"strategy_a", "strategy_b", "strategy_c"}
