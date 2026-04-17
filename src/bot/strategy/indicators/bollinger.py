"""Bollinger Bands indicator."""

from __future__ import annotations

import math


def calculate_bollinger(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float]:
    """Calculate Bollinger Bands.

    Middle = SMA(close, period)
    Upper = Middle + num_std * StdDev
    Lower = Middle - num_std * StdDev

    Args:
        closes: List of close prices.
        period: SMA period.
        num_std: Number of standard deviations.

    Returns:
        (upper, middle, lower) tuple, or (0, 0, 0) if insufficient data.
    """
    if len(closes) < period:
        return 0.0, 0.0, 0.0

    recent = closes[-period:]
    middle = sum(recent) / period
    variance = sum((x - middle) ** 2 for x in recent) / period
    std_dev = math.sqrt(variance)

    upper = middle + num_std * std_dev
    lower = middle - num_std * std_dev

    return upper, middle, lower


def calculate_rsi(
    closes: list[float],
    period: int = 14,
) -> float:
    """Calculate RSI (Relative Strength Index).

    Args:
        closes: List of close prices.
        period: RSI period.

    Returns:
        RSI value (0-100), or 50.0 if insufficient data.
    """
    if len(closes) < period + 1:
        return 50.0

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent_changes = changes[-period:]

    gains = [c for c in recent_changes if c > 0]
    losses = [-c for c in recent_changes if c < 0]

    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_ema(closes: list[float], period: int) -> float:
    """Calculate Exponential Moving Average.

    Args:
        closes: List of close prices.
        period: EMA period.

    Returns:
        EMA value, or 0.0 if insufficient data.
    """
    if len(closes) < period:
        return 0.0

    multiplier = 2.0 / (period + 1)

    # Start with SMA
    ema = sum(closes[:period]) / period

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    """Calculate Average Directional Index (ADX).

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        period: ADX period.

    Returns:
        ADX value (0-100), or 0.0 if insufficient data.
    """
    n = len(highs)
    if n < period * 2:
        return 0.0

    # Calculate +DM and -DM
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        pdm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        mdm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0
        plus_dm.append(pdm)
        minus_dm.append(mdm)

        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hc, lc))

    if len(tr_list) < period:
        return 0.0

    # Smoothed averages (Wilder's smoothing)
    atr = sum(tr_list[:period]) / period
    plus_di_smooth = sum(plus_dm[:period]) / period
    minus_di_smooth = sum(minus_dm[:period]) / period

    dx_values = []

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_smooth = (plus_di_smooth * (period - 1) + plus_dm[i]) / period
        minus_di_smooth = (minus_di_smooth * (period - 1) + minus_dm[i]) / period

        if atr > 0:
            plus_di = 100 * plus_di_smooth / atr
            minus_di = 100 * minus_di_smooth / atr
        else:
            plus_di = 0.0
            minus_di = 0.0

        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx = 100 * abs(plus_di - minus_di) / di_sum
        else:
            dx = 0.0
        dx_values.append(dx)

    if len(dx_values) < period:
        return 0.0

    # ADX = smoothed average of DX
    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period

    return adx
