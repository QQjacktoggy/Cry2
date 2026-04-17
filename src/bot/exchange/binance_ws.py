"""Binance Futures WebSocket wrapper.

Handles kline streams, user data streams, and automatic reconnection.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import structlog

from bot.exchange.testnet import EndpointConfig

logger = structlog.get_logger(__name__)


class BinanceWebSocket:
    """WebSocket client for Binance Futures streams."""

    def __init__(
        self,
        mode: str = "testnet",
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.endpoint = EndpointConfig(mode)
        self.on_message = on_message
        self._ws: Any = None
        self._running = False
        self._reconnect_count = 0
        self._max_reconnects = 10

    async def subscribe_klines(
        self,
        symbols: list[str],
        timeframe: str = "1m",
    ) -> None:
        """Subscribe to kline streams for multiple symbols."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets_package_not_installed")
            return

        streams = [f"{s.lower()}@kline_{timeframe}" for s in symbols]
        url = f"{self.endpoint.ws_url}/stream?streams={'/'.join(streams)}"

        self._running = True
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_count = 0
                    logger.info("ws_kline_connected", symbols=symbols)

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            if self.on_message:
                                self.on_message(data)
                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                self._reconnect_count += 1
                if self._reconnect_count > self._max_reconnects:
                    logger.error("ws_reconnect_limit", error=str(e))
                    break
                delay = min(2 ** self._reconnect_count, 60)
                logger.warning("ws_reconnecting", attempt=self._reconnect_count, delay=delay)
                await asyncio.sleep(delay)

    async def subscribe_user_data(self, listen_key: str) -> None:
        """Subscribe to user data stream (fills, account updates)."""
        try:
            import websockets
        except ImportError:
            return

        url = f"{self.endpoint.ws_url}/ws/{listen_key}"
        self._running = True

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("ws_user_data_connected")
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            if self.on_message:
                                self.on_message(data)
                        except json.JSONDecodeError:
                            pass
            except Exception:
                self._reconnect_count += 1
                if self._reconnect_count > self._max_reconnects:
                    break
                delay = min(2 ** self._reconnect_count, 60)
                await asyncio.sleep(delay)

    def stop(self) -> None:
        """Stop all WebSocket connections."""
        self._running = False
        logger.info("ws_stopped")
