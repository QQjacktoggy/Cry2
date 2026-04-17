"""Average True Range (ATR) indicator."""

from __future__ import annotations


def calculate_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    """Calculate the Average True Range.

    ATR = SMA of True Range over `period` bars.
    True Range = max(H-L, |H-prevC|, |L-prevC|)

    Args:
        highs: List of high prices.
        lows: List of low prices.
        closes: List of close prices.
        period: ATR period.

    Returns:
        ATR value, or 0.0 if insufficient data.
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(hl, hc, lc))

    if len(true_ranges) < period:
        return 0.0

    # Use last `period` true ranges
    return sum(true_ranges[-period:]) / period


def calculate_atr_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float]:
    """Calculate ATR series using exponential smoothing.

    Returns:
        List of ATR values (same length as input, padded with 0.0).
    """
    n = len(highs)
    if n < 2:
        return [0.0] * n

    true_ranges = [0.0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(hl, hc, lc))

    atr_values = [0.0] * n
    if n <= period:
        return atr_values

    # Initial ATR = SMA
    atr_values[period] = sum(true_ranges[1 : period + 1]) / period

    # Subsequent ATR = EMA-like smoothing
    for i in range(period + 1, n):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + true_ranges[i]) / period

    return atr_values
