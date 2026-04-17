"""Donchian Channel indicator."""

from __future__ import annotations


def calculate_donchian(
    highs: list[float],
    lows: list[float],
    period: int = 20,
) -> tuple[float, float, float]:
    """Calculate Donchian Channel.

    Upper = highest high over period
    Lower = lowest low over period
    Middle = (upper + lower) / 2

    Args:
        highs: List of high prices.
        lows: List of low prices.
        period: Lookback period.

    Returns:
        (upper, lower, middle) tuple, or (0, 0, 0) if insufficient data.
    """
    if len(highs) < period or len(lows) < period:
        return 0.0, 0.0, 0.0

    recent_highs = highs[-period:]
    recent_lows = lows[-period:]

    upper = max(recent_highs)
    lower = min(recent_lows)
    middle = (upper + lower) / 2.0

    return upper, lower, middle
