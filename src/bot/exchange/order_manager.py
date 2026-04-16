"""Order management - placing, cancelling, and tracking orders."""

from __future__ import annotations

from typing import Any

import structlog

from bot.core.constants import OrderSide, OrderType
from bot.core.types import Order, SymbolInfo
from bot.exchange.binance_rest import BinanceRestClient
from bot.utils.id_generator import generate_client_order_id

logger = structlog.get_logger(__name__)


class OrderManager:
    """Manages order lifecycle through Binance REST API."""

    def __init__(self, client: BinanceRestClient) -> None:
        self.client = client
        self._open_orders: dict[str, Order] = {}

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float = 0.0,
        stop_price: float = 0.0,
        reduce_only: bool = False,
        post_only: bool = False,
        strategy_name: str = "",
        symbol_info: SymbolInfo | None = None,
    ) -> Order:
        """Place an order with validation.

        Args:
            symbol: Trading pair.
            side: BUY or SELL.
            order_type: Market, limit, etc.
            quantity: Order quantity.
            price: Limit price (if applicable).
            stop_price: Stop trigger price.
            reduce_only: Only reduce position.
            post_only: Only maker orders.
            strategy_name: Strategy that generated this order.
            symbol_info: Contract specs for validation.

        Returns:
            Order object with exchange-assigned ID.
        """
        # Validate against symbol info
        if symbol_info:
            quantity = symbol_info.round_quantity(quantity)
            if price > 0:
                price = symbol_info.round_price(price)
            if stop_price > 0:
                stop_price = symbol_info.round_price(stop_price)

            valid, reason = symbol_info.validate_order(quantity, price or 1.0)
            if not valid:
                raise ValueError(f"Order validation failed: {reason}")

        client_order_id = generate_client_order_id(strategy_name)

        result = self.client.place_order(
            symbol=symbol,
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            price=price if price > 0 else None,
            stop_price=stop_price if stop_price > 0 else None,
            reduce_only=reduce_only,
            post_only=post_only,
            client_order_id=client_order_id,
        )

        order = Order(
            order_id=str(result.get("orderId", "")),
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            post_only=post_only,
            strategy_name=strategy_name,
            status=result.get("status", "NEW"),
        )

        self._open_orders[client_order_id] = order
        return order

    def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        """Cancel an open order."""
        try:
            self.client.cancel_order(symbol=symbol, client_order_id=client_order_id)
            self._open_orders.pop(client_order_id, None)
            return True
        except Exception as e:
            logger.error("cancel_failed", order_id=client_order_id, error=str(e))
            return False

    def cancel_all(self, symbol: str) -> int:
        """Cancel all open orders for a symbol."""
        cancelled = 0
        for order_id, order in list(self._open_orders.items()):
            if order.symbol == symbol:
                if self.cancel_order(symbol, order_id):
                    cancelled += 1
        return cancelled

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Get all tracked open orders."""
        if symbol:
            return [o for o in self._open_orders.values() if o.symbol == symbol]
        return list(self._open_orders.values())
