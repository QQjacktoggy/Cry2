"""Unit tests for LiveExecutor leverage hook."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

import pytest

from bot.core.constants import OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import OrderEvent
from bot.execution.executor_live import LiveExecutor


def _make_order(
    symbol: str = "BTCUSDT",
    requested_leverage: int = 0,
    side: OrderSide = OrderSide.BUY,
) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime.now(UTC),
        strategy_name="test",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=0.001,
        requested_leverage=requested_leverage,
        source="test",
    )


def _make_executor() -> tuple[LiveExecutor, MagicMock]:
    bus = EventBus()
    client = MagicMock()
    client.place_order.return_value = {"orderId": "123", "executedQty": "0.001", "avgPrice": "50000"}
    client.get_quantity_precision = MagicMock(return_value=3)
    executor = LiveExecutor(event_bus=bus, client=client, publish_market_fills=False)
    return executor, client


class TestExecutorLeverageHook:
    def test_set_leverage_called_when_requested_leverage_nonzero(self):
        executor, client = _make_executor()
        order = _make_order(requested_leverage=7)
        executor.submit_order(order)
        client.set_leverage.assert_called_once_with("BTCUSDT", 7)

    def test_set_leverage_not_called_when_zero(self):
        executor, client = _make_executor()
        order = _make_order(requested_leverage=0)
        executor.submit_order(order)
        client.set_leverage.assert_not_called()

    def test_set_leverage_called_before_place_order(self):
        executor, client = _make_executor()
        order_of_calls: list[str] = []
        client.set_leverage.side_effect = lambda *a, **kw: order_of_calls.append("set_leverage")
        client.place_order.side_effect = lambda *a, **kw: (
            order_of_calls.append("place_order")
            or {"orderId": "123", "executedQty": "0.001", "avgPrice": "50000"}
        )

        order = _make_order(requested_leverage=7)
        executor.submit_order(order)

        assert order_of_calls == ["set_leverage", "place_order"]

    def test_place_order_still_called_when_set_leverage_fails(self):
        executor, client = _make_executor()
        client.set_leverage.side_effect = Exception("leverage API error")
        order = _make_order(requested_leverage=7)
        # Should not raise; place_order should still be attempted
        executor.submit_order(order)
        client.place_order.assert_called_once()

    def test_conservative_leverage_1x_is_applied(self):
        executor, client = _make_executor()
        order = _make_order(requested_leverage=1)
        executor.submit_order(order)
        client.set_leverage.assert_called_once_with("BTCUSDT", 1)

    def test_leverage_set_per_symbol(self):
        executor, client = _make_executor()
        order_btc = _make_order(symbol="BTCUSDT", requested_leverage=7)
        order_eth = _make_order(symbol="ETHUSDT", requested_leverage=3)
        executor.submit_order(order_btc)
        executor.submit_order(order_eth)
        client.set_leverage.assert_any_call("BTCUSDT", 7)
        client.set_leverage.assert_any_call("ETHUSDT", 3)

    def test_order_event_has_requested_leverage_field(self):
        order = _make_order(requested_leverage=5)
        assert order.requested_leverage == 5

    def test_order_event_requested_leverage_defaults_to_zero(self):
        order = _make_order()
        assert order.requested_leverage == 0
