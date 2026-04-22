"""Tests for Binance user data stream fill parsing and status callbacks."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.core.constants import OrderSide
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent
from bot.exchange.user_data_stream import UserDataStream


def test_order_trade_update_publishes_fill_event() -> None:
    event_bus = EventBus()
    received: list[FillEvent] = []
    event_bus.subscribe("FILL", received.append)

    rest_client = MagicMock()
    stream = UserDataStream(
        event_bus=event_bus,
        rest_client=rest_client,
        known_strategies=["bridge_trend_donchian_btc"],
    )

    stream._handle_message(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "x": "TRADE",
                "s": "BTCUSDT",
                "S": "BUY",
                "c": "bot_bridge_t_1234567890_abcd12",
                "l": "0.01",
                "L": "50000.0",
                "n": "0.1",
                "N": "USDT",
                "i": 12345,
                "rp": "12.5",
                "T": 1_717_286_400_000,
            },
        }
    )

    assert len(received) == 1
    fill = received[0]
    assert fill.symbol == "BTCUSDT"
    assert fill.side == OrderSide.BUY
    assert fill.quantity == 0.01
    assert fill.price == 50000.0
    assert fill.strategy_name == "bridge_trend_donchian_btc"


def test_non_trade_update_is_ignored() -> None:
    event_bus = EventBus()
    received: list[FillEvent] = []
    event_bus.subscribe("FILL", received.append)

    stream = UserDataStream(event_bus=event_bus, rest_client=MagicMock())
    stream._handle_message({"e": "ORDER_TRADE_UPDATE", "o": {"x": "NEW"}})

    assert received == []


@pytest.mark.asyncio
async def test_stop_marks_stream_unhealthy() -> None:
    event_bus = EventBus()
    statuses: list[bool] = []
    stream = UserDataStream(
        event_bus=event_bus,
        rest_client=MagicMock(),
        on_status_change=statuses.append,
    )
    stream._ws = MagicMock()
    stream._ws.close = AsyncMock()

    await stream.stop()

    assert statuses[-1] is False
