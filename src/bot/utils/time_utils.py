"""Time conversion utilities (ms/datetime/timezone)."""

from __future__ import annotations

from datetime import UTC, datetime


def ms_to_datetime(timestamp_ms: int) -> datetime:
    """Convert Unix milliseconds to UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to Unix milliseconds."""
    return int(dt.timestamp() * 1000)


def format_timestamp(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format datetime as string."""
    return dt.strftime(fmt)


def parse_date(date_str: str) -> datetime:
    """Parse date string (YYYY-MM-DD) to UTC datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)


def get_current_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)


def ms_to_timeframe_str(ms: int) -> str:
    """Convert milliseconds to human-readable timeframe string."""
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"
