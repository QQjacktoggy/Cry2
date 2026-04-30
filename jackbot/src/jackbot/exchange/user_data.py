"""User Data Stream for Jackbot_V1.

Listens to order fill events from Binance Futures and emits FillEvent.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Callable

import structlog
import websockets

from jackbot.core.events import FillEvent

logger = structlog.get_logger("jackbot")

# Re-use URLs from feed.py logic or define here
TESTNET_WS_BASE = "wss://fstream.binancefuture.com/ws"
MAINNET_WS_BASE = "wss://fstream.binance.com/ws"


class UserDataStream:
    """WebSocket stream for account events (fills, etc.)."""

    def __init__(
        self,
        api_client: Any,
        testnet: bool = True,
        ws_url: str = "",
    ) -> None:
        self._api = api_client
        
        if ws_url:
            self._ws_url_base = ws_url if ws_url.endswith("/ws") else f"{ws_url}/ws"
        else:
            self._ws_url_base = TESTNET_WS_BASE if testnet else MAINNET_WS_BASE

        self._running = False
        self._listen_key = ""
        self.on_fill: Callable[[FillEvent], None] | None = None

    async def start(self) -> None:
        """Start the stream and keep-alive loop."""
        try:
            self._listen_key = self._api.get_listen_key()
        except Exception as e:
            logger.error("user_data_stream_key_error", error=str(e))
            return

        if not self._listen_key:
            logger.error("user_data_stream_failed_no_key")
            return

        self._running = True
        url = f"{self._ws_url_base}/{self._listen_key}"

        logger.info("user_data_stream_starting", url=f"{self._ws_url_base}/<key>")

        await asyncio.gather(
            self._listen_loop(url),
            self._keepalive_loop(),
        )

    async def stop(self) -> None:
        self._running = False
        logger.info("user_data_stream_stopping")

    async def _listen_loop(self, url: str) -> None:
        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    logger.info("user_data_stream_connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._process_message(raw)
            except websockets.ConnectionClosed:
                if self._running:
                    logger.warning("user_data_stream_reconnecting")
                    # Refresh key on reconnect
                    self._listen_key = self._api.get_listen_key()
                    url = f"{self._ws_url_base}/{self._listen_key}"
                    await asyncio.sleep(3)
            except Exception as e:
                logger.error("user_data_stream_error", error=str(e))
                if self._running:
                    await asyncio.sleep(5)

    async def _keepalive_loop(self) -> None:
        """Ping listenKey every 30 minutes."""
        while self._running:
            await asyncio.sleep(30 * 60)
            if self._running:
                success = self._api.keep_alive_listen_key()
                if success:
                    logger.info("user_data_stream_keepalive_ok")
                else:
                    logger.warning("user_data_stream_keepalive_failed")

    def _process_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            event_type = msg.get("e")

            if event_type == "ORDER_TRADE_UPDATE":
                order = msg.get("o", {})
                exec_type = order.get("x")
                
                # Only care about actual trades (fills)
                if exec_type == "TRADE":
                    fill = FillEvent(
                        timestamp=datetime.fromtimestamp(order["T"] / 1000, tz=UTC),
                        symbol=order["s"].upper(),
                        side=order["S"],
                        quantity=float(order["l"]),
                        price=float(order["L"]),
                        commission=float(order.get("n", 0)),
                        realized_pnl=float(order.get("rp", 0)),
                        order_id=str(order["i"]),
                        client_order_id=order.get("c", ""),
                        source="user_data",
                    )
                    logger.info(
                        "fill_received",
                        symbol=fill.symbol,
                        side=fill.side,
                        qty=fill.quantity,
                        price=fill.price,
                        pnl=fill.realized_pnl,
                    )
                    if self.on_fill:
                        self.on_fill(fill)

            elif event_type == "listenKeyExpired":
                logger.warning("listen_key_expired_restarting")
                self._running = False # This will trigger reconnect logic if handled properly, 
                                      # but here we'll just wait for the loop to restart it.

        except Exception as e:
            logger.error("user_data_parse_error", error=str(e))
