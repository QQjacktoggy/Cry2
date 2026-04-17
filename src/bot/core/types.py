"""Core Pydantic models for Position, Order, Fill, etc."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from bot.core.constants import OrderSide, OrderType, PositionSide


class Position(BaseModel):
    """Represents a single position."""

    symbol: str
    side: PositionSide = PositionSide.FLAT
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    leverage: int = 1
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    liquidation_price: float = 0.0
    margin: float = 0.0
    strategy_name: str = ""
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def notional_value(self) -> float:
        """Calculate notional value of the position."""
        return abs(self.quantity) * self.current_price

    @property
    def is_open(self) -> bool:
        """Check if position is open."""
        return self.side != PositionSide.FLAT and self.quantity != 0.0


class Order(BaseModel):
    """Represents an order."""

    order_id: str = ""
    client_order_id: str = ""
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float = 0.0
    stop_price: float = 0.0
    reduce_only: bool = False
    post_only: bool = False
    strategy_name: str = ""
    status: str = "NEW"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Fill(BaseModel):
    """Represents a trade execution (fill)."""

    fill_id: str = ""
    order_id: str = ""
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    commission_asset: str = "USDT"
    realized_pnl: float = 0.0
    strategy_name: str = ""
    timestamp: datetime | None = None


class AccountSnapshot(BaseModel):
    """Daily account state snapshot."""

    timestamp: datetime
    total_equity: float
    available_balance: float
    total_unrealized_pnl: float
    total_realized_pnl: float
    positions: list[Position] = Field(default_factory=list)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SymbolInfo(BaseModel):
    """Contract specification for a symbol."""

    symbol: str
    base_asset: str = ""
    quote_asset: str = "USDT"
    price_precision: int = 2
    quantity_precision: int = 3
    tick_size: float = 0.01
    step_size: float = 0.001
    min_quantity: float = 0.001
    min_notional: float = 5.0
    max_leverage: int = 125

    def round_price(self, price: float) -> float:
        """Round price to tick size."""
        return round(round(price / self.tick_size) * self.tick_size, self.price_precision)

    def round_quantity(self, quantity: float) -> float:
        """Round quantity to step size."""
        return round(
            round(quantity / self.step_size) * self.step_size, self.quantity_precision
        )

    def validate_order(self, quantity: float, price: float) -> tuple[bool, str]:
        """Validate order against contract specs."""
        if quantity < self.min_quantity:
            return False, f"Quantity {quantity} below minimum {self.min_quantity}"
        notional = quantity * price
        if notional < self.min_notional:
            return False, f"Notional {notional} below minimum {self.min_notional}"
        return True, ""
