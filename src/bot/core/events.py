"""Event type definitions for the event-driven architecture.

All modules communicate through the Event Bus using these event types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from bot.core.constants import EventType, OrderSide, OrderType


class BaseEvent(BaseModel):
    """Base event with common fields."""

    event_type: EventType
    timestamp: datetime
    source: str = ""

    model_config = {"frozen": True}


class MarketEvent(BaseEvent):
    """New K-line or tick data arrived."""

    event_type: EventType = EventType.MARKET
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0
    trade_count: int = 0
    taker_buy_volume: float = 0.0
    taker_buy_quote_volume: float = 0.0
    bar_timestamp: datetime | None = None


class FundingEvent(BaseEvent):
    """Funding rate settlement event."""

    event_type: EventType = EventType.FUNDING
    symbol: str
    funding_rate: float
    next_funding_time: datetime | None = None


class SignalEvent(BaseEvent):
    """Strategy generates a trading signal."""

    event_type: EventType = EventType.SIGNAL
    strategy_name: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    reduce_only: bool = False
    post_only: bool = False
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderEvent(BaseEvent):
    """Order instruction after risk check passes."""

    event_type: EventType = EventType.ORDER
    strategy_name: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float = 0.0
    stop_price: float = 0.0
    reduce_only: bool = False
    post_only: bool = False
    client_order_id: str = ""
    requested_leverage: int = 0  # 0 = no change; >0 = set before placing


class FillEvent(BaseEvent):
    """Order filled (execution report)."""

    event_type: EventType = EventType.FILL
    fill_id: str = ""
    strategy_name: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    commission_asset: str = "USDT"
    order_id: str = ""
    client_order_id: str = ""
    realized_pnl: float = 0.0


class RejectEvent(BaseEvent):
    """Order rejected."""

    event_type: EventType = EventType.REJECT
    strategy_name: str
    symbol: str
    reason: str
    client_order_id: str = ""


class LiquidationEvent(BaseEvent):
    """Liquidation warning."""

    event_type: EventType = EventType.LIQUIDATION
    symbol: str
    margin_ratio: float
    maintenance_margin: float
    current_margin: float


class KillSwitchEvent(BaseEvent):
    """Emergency stop - close all positions and halt."""

    event_type: EventType = EventType.KILL_SWITCH
    reason: str
    triggered_by: str = "system"
    close_all: bool = True


class DailyTargetHitEvent(BaseEvent):
    """Fired once per day when daily realized PnL reaches the profit target."""

    event_type: EventType = EventType.DAILY_TARGET_HIT
    daily_pnl: float
    target_usd: float
