"""Integration tests for LiveExecutor with a mock Binance REST client."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.core.constants import OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, OrderEvent, RejectEvent
from bot.execution.executor_live import LiveExecutor


def _make_order(
    symbol: str = "BTCUSDT",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    qty: float = 0.01,
    price: float = 0.0,
    strategy: str = "trend",
) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        strategy_name=strategy,
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=qty,
        price=price,
        client_order_id="",
        source="test",
    )


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.place_order.return_value = {
        "orderId": "12345",
        "executedQty": "0.01",
        "avgPrice": "50000.0",
        "status": "FILLED",
    }
    client.get_account_trades.return_value = []
    return client


@pytest.fixture
def executor(event_bus, mock_client):
    return LiveExecutor(event_bus=event_bus, client=mock_client)


class TestMarketOrderFill:
    def test_market_buy_publishes_fill_event(self, executor, event_bus, mock_client):
        received: list[FillEvent] = []
        event_bus.subscribe("FILL", received.append)

        order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET)
        executor.submit_order(order)

        assert len(received) == 1
        fill = received[0]
        assert fill.symbol == "BTCUSDT"
        assert fill.side == OrderSide.BUY
        assert fill.quantity == 0.01
        assert fill.price == 50000.0

    def test_market_sell_publishes_fill_event(self, executor, event_bus):
        received: list[FillEvent] = []
        event_bus.subscribe("FILL", received.append)

        order = _make_order(side=OrderSide.SELL, order_type=OrderType.MARKET)
        executor.submit_order(order)

        assert len(received) == 1
        assert received[0].side == OrderSide.SELL

    def test_fill_carries_strategy_name(self, executor, event_bus):
        received: list[FillEvent] = []
        event_bus.subscribe("FILL", received.append)

        order = _make_order(strategy="grid_futures")
        executor.submit_order(order)

        assert received[0].strategy_name == "grid_futures"

    def test_fill_carries_order_id(self, executor, event_bus):
        received: list[FillEvent] = []
        event_bus.subscribe("FILL", received.append)

        executor.submit_order(_make_order())

        assert received[0].order_id == "12345"


class TestOrderFailure:
    def test_exchange_error_publishes_reject_event(self, executor, event_bus, mock_client):
        mock_client.place_order.side_effect = RuntimeError("Insufficient margin")

        rejected: list[RejectEvent] = []
        event_bus.subscribe("REJECT", rejected.append)

        executor.submit_order(_make_order())

        assert len(rejected) == 1
        assert "Insufficient margin" in rejected[0].reason

    def test_no_fill_on_error(self, executor, event_bus, mock_client):
        mock_client.place_order.side_effect = RuntimeError("API error")

        fills: list[FillEvent] = []
        event_bus.subscribe("FILL", fills.append)

        executor.submit_order(_make_order())

        assert len(fills) == 0

    def test_returns_client_order_id_on_error(self, executor, mock_client):
        mock_client.place_order.side_effect = RuntimeError("error")
        result = executor.submit_order(_make_order())
        assert isinstance(result, str)
        assert len(result) > 0


class TestCancelOrder:
    def test_cancel_known_order(self, executor, event_bus, mock_client):
        received: list[FillEvent] = []
        event_bus.subscribe("FILL", received.append)

        executor.submit_order(_make_order())
        fill = received[0]
        order_id = fill.order_id  # "12345"

        mock_client.cancel_order = MagicMock(return_value={"status": "CANCELED"})
        executor.order_manager.cancel_order = MagicMock(return_value=True)

        result = executor.cancel_order(order_id)
        assert result is True

    def test_cancel_unknown_order_returns_false(self, executor):
        result = executor.cancel_order("UNKNOWN-ID-9999")
        assert result is False


class TestLimitOrderNoFill:
    def test_limit_order_does_not_publish_fill(self, executor, event_bus):
        """Limit orders are not immediately filled — no FillEvent on submit."""
        fills: list[FillEvent] = []
        event_bus.subscribe("FILL", fills.append)

        order = _make_order(order_type=OrderType.LIMIT, price=49000.0)
        executor.submit_order(order)

        assert len(fills) == 0
