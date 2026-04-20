"""Live executor - sends orders to Binance REST API.

Receives OrderEvents from the event bus, translates them to API calls,
and publishes FillEvents on completion.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from bot.core.constants import OrderType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, OrderEvent, RejectEvent
from bot.core.types import Position
from bot.exchange.account import AccountManager
from bot.exchange.binance_rest import BinanceRestClient
from bot.exchange.order_manager import OrderManager
from bot.execution.executor_base import BaseExecutor
from bot.utils.id_generator import generate_client_order_id

logger = structlog.get_logger(__name__)


class LiveExecutor(BaseExecutor):
    """Live executor that sends orders to Binance."""

    def __init__(
        self,
        event_bus: EventBus,
        client: BinanceRestClient,
    ) -> None:
        self.event_bus = event_bus
        self.client = client
        self.order_manager = OrderManager(client)
        self.account_manager = AccountManager(client)
        self._order_symbols: dict[str, str] = {}  # order_id → symbol

    def submit_order(self, order: OrderEvent) -> str:
        """Submit order to Binance."""
        client_order_id = order.client_order_id or generate_client_order_id(order.strategy_name)

        try:
            result = self.client.place_order(
                symbol=order.symbol,
                side=order.side.value,
                order_type=order.order_type.value,
                quantity=order.quantity,
                price=order.price if order.price > 0 else None,
                stop_price=order.stop_price if order.stop_price > 0 else None,
                reduce_only=order.reduce_only,
                post_only=order.post_only,
                client_order_id=client_order_id,
            )

            # Track order → symbol mapping for cancel
            exchange_order_id = str(result.get("orderId", ""))
            if exchange_order_id:
                self._order_symbols[exchange_order_id] = order.symbol
            self._order_symbols[client_order_id] = order.symbol

            # If market order, it should be filled immediately
            if order.order_type == OrderType.MARKET:
                fill = FillEvent(
                    timestamp=datetime.now(UTC),
                    strategy_name=order.strategy_name,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=float(result.get("executedQty", order.quantity)),
                    price=float(result.get("avgPrice", 0)),
                    commission=0.0,
                    order_id=exchange_order_id,
                    client_order_id=client_order_id,
                    source="live",
                )
                self.event_bus.publish(fill)

            logger.info(
                "live_order_submitted",
                symbol=order.symbol,
                side=order.side.value,
                qty=order.quantity,
                order_id=result.get("orderId"),
            )
            return client_order_id

        except Exception as e:
            reject = RejectEvent(
                timestamp=datetime.now(UTC),
                strategy_name=order.strategy_name,
                symbol=order.symbol,
                reason=str(e),
                client_order_id=client_order_id,
                source="live",
            )
            self.event_bus.publish(reject)
            logger.error("live_order_failed", symbol=order.symbol, error=str(e))
            return client_order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order on Binance."""
        try:
            symbol = self._order_symbols.get(order_id, "")
            if not symbol:
                logger.warning("cancel_no_symbol", order_id=order_id)
                return False
            return self.order_manager.cancel_order(symbol, order_id)
        except Exception as e:
            logger.error("cancel_failed", order_id=order_id, error=str(e))
            return False

    def get_position(self, symbol: str) -> Position:
        """Get position from Binance."""
        return self.account_manager.get_position(symbol)

    def get_balance(self) -> float:
        """Get USDT balance from Binance."""
        return self.account_manager.get_balance()

    def get_equity(self) -> float:
        """Get total equity from Binance."""
        return self.account_manager.get_equity()
