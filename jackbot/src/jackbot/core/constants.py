"""Constants and enumerations for Jackbot_V1."""

from __future__ import annotations

from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    NEUTRAL = "neutral"


class GridDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class TradingMode(str, Enum):
    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"


class GridLevelState(str, Enum):
    """State of a single grid level."""
    PENDING_BUY = "pending_buy"      # Waiting for BUY limit fill
    PENDING_SELL = "pending_sell"     # Waiting for SELL limit fill
    FILLED_BUY = "filled_buy"        # BUY filled, waiting to place SELL
    FILLED_SELL = "filled_sell"       # SELL filled, waiting to place BUY
    MATCHED = "matched"              # Both sides filled, profit captured
    CANCELLED = "cancelled"
