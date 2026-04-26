"""Global constants and enumerations."""

from enum import Enum


class OrderSide(str, Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class PositionSide(str, Enum):
    """Position side."""
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class TimeFrame(str, Enum):
    """K-line timeframes."""
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def minutes(self) -> int:
        """Return the number of minutes in this timeframe."""
        mapping = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        return mapping[self.value]

    @property
    def milliseconds(self) -> int:
        """Return the number of milliseconds in this timeframe."""
        return self.minutes * 60 * 1000


class EventType(str, Enum):
    """Event types for the event bus."""
    MARKET = "MARKET"
    FUNDING = "FUNDING"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    REJECT = "REJECT"
    LIQUIDATION = "LIQUIDATION"
    KILL_SWITCH = "KILL_SWITCH"
    DAILY_TARGET_HIT = "DAILY_TARGET_HIT"


# API Constants
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com"
BINANCE_FUTURES_WS_TESTNET_URL = "wss://fstream.binancefuture.com"

# Funding rate settlement interval (8 hours in milliseconds)
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000

# Default risk limits
DEFAULT_MAX_LEVERAGE = 3
HARD_MAX_LEVERAGE = 5
DEFAULT_MAX_RISK_PER_TRADE_PCT = 1.0
