"""Binance Futures User Data Stream.

Subscribes to the Binance user data WebSocket (`/ws/<listenKey>`) to receive
real-time account events: order fills, position changes, account balance updates.

Parsed ``ORDER_TRADE_UPDATE`` events are converted to ``FillEvent`` and published
to the ``EventBus``.  The listen key is refreshed every 30 minutes as required
by Binance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from bot.core.constants import OrderSide
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent
from bot.exchange.binance_rest import BinanceRestClient
from bot.utils.id_generator import extract_strategy_from_client_oid

logger = structlog.get_logger(__name__)

# Binance requires keep-alive every 60 min; we refresh at 30 min to be safe.
_KEEPALIVE_INTERVAL = 30 * 60  # seconds
_RECONNECT_BASE_DELAY = 1.0
_MAX_RECONNECT_DELAY = 60.0
_MAX_RECONNECT_ATTEMPTS = 10


class UserDataStream:
    """Listens to Binance Futures user data WebSocket and publishes FillEvents.

    Args:
        event_bus: Application event bus.
        rest_client: BinanceRestClient instance (for listen key management).
        ws_url: Base WebSocket URL (without trailing slash).
        known_strategies: List of strategy names for client_order_id inference.
        on_fill: Optional additional callback invoked on each fill.
    """

    def __init__(
        self,
        event_bus: EventBus,
        rest_client: BinanceRestClient,
        ws_url: str = "wss://fstream.binance.com",
        known_strategies: list[str] | None = None,
        on_fill: Callable[[FillEvent], None] | None = None,
        on_status_change: Callable[[bool], None] | None = None,
    ) -> None:
        self.event_bus = event_bus
        self._rest = rest_client
        self._ws_url = ws_url
        self._known_strategies = known_strategies or []
        self._on_fill = on_fill
        self._on_status_change = on_status_change

        self._listen_key: str = ""
        self._ws: Any = None
        self._running: bool = False
        self._reconnect_attempts: int = 0

    # ── Public interface ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the user data stream (runs until stop() is called)."""
        self._running = True
        self._listen_key = self._rest.get_listen_key()
        if not self._listen_key:
            logger.error("user_data_stream_no_listen_key")
            self._running = False
            self._notify_status_change(False)
            return
        logger.info("user_data_stream_started", listen_key=self._listen_key[:8] + "...")

        await asyncio.gather(
            self._run_ws(),
            self._keepalive_loop(),
        )

    async def stop(self) -> None:
        """Stop the stream gracefully."""
        self._running = False
        if self._ws:
            with suppress(Exception):
                await self._ws.close()
        self._notify_status_change(False)
        logger.info("user_data_stream_stopped")

    # ── Internal WebSocket loop ─────────────────────────────────────────────

    async def _run_ws(self) -> None:
        """Connect and maintain the WebSocket connection with reconnect logic."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets package not installed — user data stream disabled")
            return

        while self._running:
            url = f"{self._ws_url}/ws/{self._listen_key}"
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0
                    self._notify_status_change(True)
                    logger.info("user_data_stream_connected", url=url[:60])

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._handle_message(msg)
                        except Exception as exc:
                            logger.warning("user_data_msg_parse_error", error=str(exc))

            except Exception as exc:
                self._notify_status_change(False)
                if not self._running:
                    break
                self._reconnect_attempts += 1
                if self._reconnect_attempts > _MAX_RECONNECT_ATTEMPTS:
                    logger.error("user_data_stream_max_reconnect_exceeded")
                    self._running = False
                    break
                delay = min(
                    _RECONNECT_BASE_DELAY * (2 ** (self._reconnect_attempts - 1)),
                    _MAX_RECONNECT_DELAY,
                )
                logger.warning(
                    "user_data_stream_reconnecting",
                    attempt=self._reconnect_attempts,
                    delay=delay,
                    error=str(exc),
                )
                # Refresh listen key on reconnect (old one may have expired)
                try:
                    self._listen_key = self._rest.get_listen_key()
                except Exception as ke:
                    logger.error("listen_key_refresh_failed", error=str(ke))

                await asyncio.sleep(delay)

    async def _keepalive_loop(self) -> None:
        """Ping the listen key every 30 minutes to prevent expiry."""
        while self._running:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            if not self._running:
                break
            try:
                self._rest.keep_alive_listen_key(self._listen_key)
                logger.debug("listen_key_keepalive_sent")
            except Exception as exc:
                logger.warning("listen_key_keepalive_failed", error=str(exc))
                # Attempt to obtain a fresh key
                try:
                    self._listen_key = self._rest.get_listen_key()
                    logger.info("listen_key_refreshed_after_keepalive_failure")
                except Exception as ke:
                    logger.error("listen_key_refresh_failed", error=str(ke))

    def _notify_status_change(self, healthy: bool) -> None:
        if self._on_status_change is not None:
            self._on_status_change(healthy)

    # ── Message parsing ─────────────────────────────────────────────────────

    def _handle_message(self, msg: dict[str, Any]) -> None:
        event_type = msg.get("e")
        if event_type == "ORDER_TRADE_UPDATE":
            self._handle_order_trade_update(msg.get("o", {}))
        elif event_type == "ACCOUNT_UPDATE":
            logger.debug("account_update_received", balances=len(msg.get("a", {}).get("B", [])))
        elif event_type == "listenKeyExpired":
            logger.warning("listen_key_expired_event — refreshing")
            try:
                self._listen_key = self._rest.get_listen_key()
            except Exception as exc:
                logger.error("listen_key_refresh_after_expiry_failed", error=str(exc))

    def _handle_order_trade_update(self, o: dict[str, Any]) -> None:
        """Parse an ORDER_TRADE_UPDATE payload and publish a FillEvent.

        Binance only sends this when an order is actually executed (filled).
        We filter on execution type == 'TRADE' to avoid partial-fill noise on
        non-executed events.
        """
        exec_type = o.get("x", "")
        if exec_type != "TRADE":
            return

        symbol = o.get("s", "")
        side_raw = o.get("S", "BUY")
        try:
            side = OrderSide(side_raw.upper())
        except ValueError:
            side = OrderSide.BUY

        client_oid = o.get("c", "")
        strategy = extract_strategy_from_client_oid(client_oid, self._known_strategies) or "unknown"

        qty = float(o.get("l", 0.0))   # last filled qty
        price = float(o.get("L", 0.0)) # last filled price
        commission = float(o.get("n", 0.0))
        commission_asset = o.get("N", "USDT") or "USDT"
        order_id = str(o.get("i", ""))
        realized_pnl = float(o.get("rp", 0.0))

        ts_ms = o.get("T", 0)
        try:
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        except Exception:
            ts = datetime.now(UTC)

        fill = FillEvent(
            timestamp=ts,
            strategy_name=strategy,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            commission=commission,
            commission_asset=commission_asset,
            order_id=order_id,
            client_order_id=client_oid,
            realized_pnl=realized_pnl,
            source="user_data_stream",
        )

        self.event_bus.publish(fill)
        logger.info(
            "fill_from_user_data_stream",
            symbol=symbol,
            side=side_raw,
            qty=qty,
            price=price,
            pnl=realized_pnl,
            strategy=strategy,
        )

        if self._on_fill:
            try:
                self._on_fill(fill)
            except Exception as exc:
                logger.warning("on_fill_callback_error", error=str(exc))
