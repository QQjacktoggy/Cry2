"""Tests for Binance user data stream fill parsing and status callbacks."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
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


def test_order_trade_update_prefers_runtime_strategy_resolver() -> None:
    event_bus = EventBus()
    received: list[FillEvent] = []
    event_bus.subscribe("FILL", received.append)

    stream = UserDataStream(
        event_bus=event_bus,
        rest_client=MagicMock(),
        known_strategies=[
            "bridge_trend_donchian_mtf_btc",
            "bridge_tail_risk_hedge_sol",
        ],
        strategy_resolver=lambda order_id, client_oid: "bridge_tail_risk_hedge_sol"
        if order_id == "12345"
        else None,
    )

    stream._handle_message(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "x": "TRADE",
                "s": "SOLUSDT",
                "S": "BUY",
                "c": "bot_bridge_t_1234567890_abcd12",
                "l": "0.01",
                "L": "100.0",
                "n": "0.1",
                "N": "USDT",
                "i": 12345,
                "rp": "0",
                "T": 1_717_286_400_000,
            },
        }
    )

    assert received[0].strategy_name == "bridge_tail_risk_hedge_sol"


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


@pytest.mark.asyncio
async def test_wait_until_healthy_returns_false_when_start_stops_immediately() -> None:
    event_bus = EventBus()
    statuses: list[bool] = []
    rest_client = MagicMock()
    rest_client.get_listen_key.return_value = ""
    stream = UserDataStream(
        event_bus=event_bus,
        rest_client=rest_client,
        on_status_change=statuses.append,
    )

    task = asyncio.create_task(stream.start())
    ready = await stream.wait_until_healthy(timeout=0.2)
    await task

    assert ready is False
    assert statuses[-1] is False


@pytest.mark.asyncio
async def test_stop_cancels_keepalive_promptly() -> None:
    event_bus = EventBus()
    rest_client = MagicMock()
    rest_client.get_listen_key.return_value = "listen-key"
    stream = UserDataStream(event_bus=event_bus, rest_client=rest_client)

    async def _idle_ws() -> None:
        while stream._running:
            await asyncio.sleep(3600)

    async def _idle_keepalive() -> None:
        while stream._running:
            await asyncio.sleep(1800)

    stream._run_ws = _idle_ws  # type: ignore[method-assign]
    stream._keepalive_loop = _idle_keepalive  # type: ignore[method-assign]

    task = asyncio.create_task(stream.start())
    await asyncio.sleep(0)

    await stream.stop()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
