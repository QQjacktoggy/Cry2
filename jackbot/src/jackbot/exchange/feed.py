"""WebSocket K-line feed for Jackbot_V1.

Connects to Binance Futures WebSocket and emits MarketEvent on each
completed candlestick bar.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Callable

import structlog
import websockets

from jackbot.core.events import MarketEvent

logger = structlog.get_logger(__name__)

BINANCE_WS_URL = "wss://fstream.binance.com/ws"
TESTNET_WS_URL = "wss://fstream.binancefuture.com/ws"


class KlineFeed:
    """WebSocket kline feed — emits MarketEvent on bar close.

    Usage:
        feed = KlineFeed(symbols=["BTCUSDT", "ETHUSDT"], timeframe="5m")
        feed.on_bar = my_handler  # receives MarketEvent
        await feed.start()
    """

    def __init__(
        self,
        symbols: list[str],
        timeframe: str = "5m",
        testnet: bool = True,
        ws_url: str = "",
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._timeframe = timeframe
        
        if ws_url:
            self._ws_url = ws_url if ws_url.endswith("/ws") else f"{ws_url}/ws"
        else:
            self._ws_url = TESTNET_WS_URL if testnet else BINANCE_WS_URL

        self._running = False
        self.on_bar: Callable[[MarketEvent], None] | None = None

    @property
    def streams(self) -> list[str]:
        return [f"{s}@kline_{self._timeframe}" for s in self._symbols]

    async def start(self) -> None:
        """Connect and listen for kline events."""
        combined_url = f"{self._ws_url}/{'/'.join(self.streams)}"
        self._running = True

        logger.info(
            "kline_feed_starting",
            symbols=self._symbols,
            timeframe=self._timeframe,
            url=combined_url,
        )

        while self._running:
            try:
                async with websockets.connect(combined_url) as ws:
                    logger.info("kline_feed_connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._process_message(raw)
            except websockets.ConnectionClosed:
                if self._running:
                    logger.warning("kline_feed_reconnecting")
                    await asyncio.sleep(3)
            except Exception as e:
                logger.error("kline_feed_error", error=str(e))
                if self._running:
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        logger.info("kline_feed_stopped")

    def _process_message(self, raw: str) -> None:
        """Parse kline message and emit MarketEvent on bar close."""
        try:
            data = json.loads(raw)
            kline = data.get("k")
            if kline is None:
                return

            # Only emit on bar close
            if not kline.get("x", False):
                return

            event = MarketEvent(
                timestamp=datetime.fromtimestamp(kline["T"] / 1000, tz=UTC),
                symbol=kline["s"].upper(),
                timeframe=kline["i"],
                open=float(kline["o"]),
                high=float(kline["h"]),
                low=float(kline["l"]),
                close=float(kline["c"]),
                volume=float(kline["v"]),
                source="ws",
            )

            if self.on_bar:
                self.on_bar(event)

        except Exception as e:
            logger.error("kline_parse_error", error=str(e))
