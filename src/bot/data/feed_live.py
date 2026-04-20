"""Live data feed - subscribes to Binance WebSocket for real-time data."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from bot.core.clock import RealClock
from bot.core.event_bus import EventBus
from bot.core.events import FundingEvent, MarketEvent
from bot.data.feed_base import DataFeed
from bot.utils.time_utils import ms_to_datetime

logger = structlog.get_logger(__name__)


class LiveFeed(DataFeed):
    """Real-time data feed via Binance WebSocket.

    Subscribes to kline streams for specified symbols and timeframes.
    Produces MarketEvent on each completed candle.
    """

    def __init__(
        self,
        event_bus: EventBus,
        clock: RealClock,
        ws_url: str = "wss://fstream.binance.com",
        symbols: list[str] | None = None,
        timeframe: str = "1m",
        rest_client: Any = None,
        funding_poll_interval: int = 300,
    ) -> None:
        super().__init__(event_bus, clock)
        self.ws_url = ws_url
        self.symbols = [s.lower() for s in (symbols or [])]
        self.timeframe = timeframe
        self._ws: Any = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 1.0
        self._rest_client = rest_client
        self._funding_poll_interval = funding_poll_interval

    def _build_stream_url(self) -> str:
        """Build combined WebSocket stream URL."""
        streams = []
        for symbol in self.symbols:
            streams.append(f"{symbol}@kline_{self.timeframe}")
        stream_path = "/".join(streams)
        return f"{self.ws_url}/stream?streams={stream_path}"

    def _parse_kline_message(self, data: dict[str, Any]) -> MarketEvent | None:
        """Parse WebSocket kline message into MarketEvent."""
        stream = data.get("stream", "")
        kline_data = data.get("data", {}).get("k", {})

        if not kline_data:
            return None

        # Only emit event on candle close
        is_closed = kline_data.get("x", False)
        if not is_closed:
            return None

        symbol = kline_data["s"]
        timestamp_ms = int(kline_data["t"])

        return MarketEvent(
            timestamp=ms_to_datetime(timestamp_ms),
            symbol=symbol,
            timeframe=self.timeframe,
            open=float(kline_data["o"]),
            high=float(kline_data["h"]),
            low=float(kline_data["l"]),
            close=float(kline_data["c"]),
            volume=float(kline_data["v"]),
            quote_volume=float(kline_data["q"]),
            trade_count=int(kline_data["n"]),
            taker_buy_volume=float(kline_data["V"]),
            taker_buy_quote_volume=float(kline_data["Q"]),
            bar_timestamp=ms_to_datetime(timestamp_ms),
            source="live_ws",
        )

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and listen for messages."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets_not_installed")
            return

        url = self._build_stream_url()
        logger.info("ws_connecting", url=url)

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0
                    logger.info("ws_connected", symbols=self.symbols)

                    async for raw_message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw_message)
                            event = self._parse_kline_message(data)
                            if event:
                                self.event_bus.publish(event)
                        except json.JSONDecodeError:
                            logger.warning("ws_invalid_json")

            except Exception as e:
                self._reconnect_attempts += 1
                if self._reconnect_attempts > self._max_reconnect_attempts:
                    logger.error("ws_max_reconnect_exceeded")
                    self._running = False
                    break
                delay = min(
                    self._reconnect_delay * (2 ** (self._reconnect_attempts - 1)),
                    60.0,
                )
                logger.warning(
                    "ws_reconnecting",
                    attempt=self._reconnect_attempts,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)

    def start(self) -> None:
        """Start the live feed (must be called from async context)."""
        self._running = True
        logger.info("live_feed_starting", symbols=self.symbols, timeframe=self.timeframe)

    async def _poll_funding_rates(self) -> None:
        """Periodically poll funding rates via REST and publish FundingEvent."""
        if self._rest_client is None:
            logger.info("funding_poll_skipped", reason="no rest_client")
            return

        while self._running:
            for symbol_lower in self.symbols:
                symbol = symbol_lower.upper()
                try:
                    data = self._rest_client.get_funding_rate(symbol)
                    rate = float(data.get("lastFundingRate", 0))
                    next_time_ms = int(data.get("nextFundingTime", 0))
                    next_time = (
                        ms_to_datetime(next_time_ms) if next_time_ms else None
                    )
                    event = FundingEvent(
                        timestamp=datetime.now(UTC),
                        symbol=symbol,
                        funding_rate=rate,
                        next_funding_time=next_time,
                        source="rest_poll",
                    )
                    self.event_bus.publish(event)
                    logger.debug("funding_rate_polled", symbol=symbol, rate=rate)
                except Exception as e:
                    logger.warning("funding_poll_error", symbol=symbol, error=str(e))
            await asyncio.sleep(self._funding_poll_interval)

    async def start_async(self) -> None:
        """Start the WebSocket connection and funding rate poller."""
        self._running = True
        tasks = [asyncio.create_task(self._connect_and_listen())]
        if self._rest_client is not None:
            tasks.append(asyncio.create_task(self._poll_funding_rates()))
        await asyncio.gather(*tasks)

    def stop(self) -> None:
        """Stop the live feed."""
        self._running = False
        logger.info("live_feed_stopped")

    def has_next(self) -> bool:
        """Live feed is event-driven, not pull-based."""
        return False

    def next(self) -> None:
        """Live feed uses push model via event bus."""
        return None
