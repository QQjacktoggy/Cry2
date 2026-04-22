"""Tests for bot.data.feed_live."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest

from bot.core.clock import RealClock
from bot.core.event_bus import EventBus
from bot.data.feed_live import LiveFeed


@pytest.mark.asyncio
async def test_stop_async_closes_websocket() -> None:
    feed = LiveFeed(event_bus=EventBus(), clock=RealClock(), symbols=["BTCUSDT"])
    feed._running = True
    ws = AsyncMock()
    feed._ws = ws

    await feed.stop_async()

    ws.close.assert_awaited_once()
    assert feed._running is False
    assert feed._ws is None


@pytest.mark.asyncio
async def test_stop_async_cancels_background_tasks_promptly() -> None:
    feed = LiveFeed(event_bus=EventBus(), clock=RealClock(), symbols=["BTCUSDT"])
    feed._rest_client = object()
    feed.periodic_sync_fn = lambda: None
    feed.periodic_sync_interval = 3600
    feed._funding_poll_interval = 3600

    async def _idle() -> None:
        while feed._running:
            await asyncio.sleep(3600)

    feed._connect_and_listen = _idle  # type: ignore[method-assign]
    feed._poll_funding_rates = _idle  # type: ignore[method-assign]
    feed._periodic_sync = _idle  # type: ignore[method-assign]

    task = asyncio.create_task(feed.start_async())
    await asyncio.sleep(0)

    await feed.stop_async()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
