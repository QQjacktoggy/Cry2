"""Custom exception types for the trading system."""


class BotError(Exception):
    """Base exception for all bot errors."""


class ConfigError(BotError):
    """Configuration error."""


class DataError(BotError):
    """Data fetching or processing error."""


class ExchangeError(BotError):
    """Exchange communication error."""


class OrderRejected(BotError):
    """Order was rejected by exchange or risk manager."""

    def __init__(self, reason: str, symbol: str = "", order_id: str = "") -> None:
        self.reason = reason
        self.symbol = symbol
        self.order_id = order_id
        super().__init__(f"Order rejected: {reason} (symbol={symbol}, id={order_id})")


class InsufficientMargin(BotError):
    """Not enough margin for the operation."""

    def __init__(self, required: float, available: float) -> None:
        self.required = required
        self.available = available
        super().__init__(f"Insufficient margin: required={required}, available={available}")


class RiskLimitExceeded(BotError):
    """Risk limit has been exceeded."""

    def __init__(self, limit_type: str, current: float, limit: float) -> None:
        self.limit_type = limit_type
        self.current = current
        self.limit = limit
        super().__init__(f"Risk limit exceeded: {limit_type} current={current} limit={limit}")


class KillSwitchActivated(BotError):
    """Kill switch has been triggered."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Kill switch activated: {reason}")


class CircuitBreakerTriggered(BotError):
    """Circuit breaker has been triggered."""

    def __init__(self, reason: str, cooldown_minutes: int) -> None:
        self.reason = reason
        self.cooldown_minutes = cooldown_minutes
        super().__init__(f"Circuit breaker: {reason} (cooldown={cooldown_minutes}min)")


class WebSocketError(BotError):
    """WebSocket connection error."""


class RateLimitError(ExchangeError):
    """API rate limit exceeded."""

    def __init__(self, retry_after: int = 0) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded, retry after {retry_after}s")
