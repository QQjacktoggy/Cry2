"""Event models for Jackbot_V1.

Lightweight Pydantic models used across the event bus.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MarketEvent(BaseModel, frozen=True):
    """A completed candlestick bar."""

    timestamp: datetime
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "ws"


class GridSignalEvent(BaseModel, frozen=True):
    """Signal to place or cancel a grid order."""

    timestamp: datetime
    symbol: str
    side: str                        # BUY / SELL
    order_type: str = "LIMIT"        # LIMIT / MARKET
    price: float = 0.0               # limit price (0 for market)
    quantity: float = 0.0
    grid_id: str = ""                # unique grid instance id
    level_index: int = -1            # grid level number
    reduce_only: bool = False
    cancel_order_id: str = ""        # non-empty → cancel this order
    metadata: dict[str, Any] = Field(default_factory=dict)


class FillEvent(BaseModel, frozen=True):
    """A fill (trade execution) report."""

    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float = 0.0
    realized_pnl: float = 0.0
    order_id: str = ""
    client_order_id: str = ""
    grid_id: str = ""
    level_index: int = -1
    source: str = "exchange"


class GridProfitEvent(BaseModel, frozen=True):
    """One grid level completed a buy+sell match."""

    timestamp: datetime
    symbol: str
    grid_id: str
    level_index: int
    buy_price: float
    sell_price: float
    quantity: float
    profit_usd: float               # sell_price - buy_price * quantity (gross)
    commission: float = 0.0          # total fee for both sides
    source: str = "grid_engine"


class StrategyPnLEvent(BaseModel, frozen=True):
    """Realized PnL contribution outside the normal matched-grid event flow."""

    timestamp: datetime
    symbol: str
    source: str
    bucket: str
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
