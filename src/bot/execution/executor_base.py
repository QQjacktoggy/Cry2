"""Abstract Executor interface shared by sim and live executors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from bot.core.events import OrderEvent
from bot.core.types import Position


class BaseExecutor(ABC):
    """Abstract base class for order execution.

    SimExecutor: simulates fills for backtesting.
    LiveExecutor: sends orders to Binance REST API.
    """

    @abstractmethod
    def submit_order(self, order: OrderEvent) -> str:
        """Submit an order for execution.

        Args:
            order: The order event to execute.

        Returns:
            Order ID string.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: The order to cancel.

        Returns:
            True if cancelled successfully.
        """

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Get current position for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Position object.
        """

    @abstractmethod
    def get_balance(self) -> float:
        """Get available USDT balance.

        Returns:
            Available balance in USDT.
        """

    @abstractmethod
    def get_equity(self) -> float:
        """Get total account equity (balance + unrealized PnL).

        Returns:
            Total equity in USDT.
        """
