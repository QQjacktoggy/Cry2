"""Trading fee model for Binance Futures.

Supports Maker/Taker fee differentiation.
Default rates: Maker 0.02%, Taker 0.04%.
"""

from __future__ import annotations

import structlog

from bot.core.constants import OrderType

logger = structlog.get_logger(__name__)


class FeeModel:
    """Calculates trading fees based on order type."""

    def __init__(
        self,
        maker_rate: float = 0.0002,
        taker_rate: float = 0.0004,
    ) -> None:
        """Initialize fee model.

        Args:
            maker_rate: Maker fee rate (default 0.02%).
            taker_rate: Taker fee rate (default 0.04%).
        """
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate

    def calculate(
        self,
        notional_value: float,
        order_type: OrderType = OrderType.MARKET,
        is_maker: bool | None = None,
    ) -> float:
        """Calculate fee for a trade.

        Args:
            notional_value: Trade notional value (quantity * price).
            order_type: Order type (determines maker/taker).
            is_maker: Override maker/taker detection.

        Returns:
            Fee amount in USDT.
        """
        if is_maker is not None:
            rate = self.maker_rate if is_maker else self.taker_rate
        elif order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            rate = self.maker_rate
        else:
            rate = self.taker_rate

        return abs(notional_value) * rate

    def get_rate(self, order_type: OrderType = OrderType.MARKET) -> float:
        """Get the fee rate for a given order type."""
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            return self.maker_rate
        return self.taker_rate
