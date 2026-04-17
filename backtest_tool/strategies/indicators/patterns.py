"""Candlestick pattern detection (pin bar, engulfing)."""

from __future__ import annotations

import pandas as pd


def detect_pin_bar(
    ohlcv: pd.DataFrame,
    wick_ratio: float = 2.0,
    body_ratio_max: float = 0.35,
) -> tuple[pd.Series, pd.Series]:
    """Detect bullish / bearish pin bars.

    Bullish pin bar: long lower wick, small body near top of bar.
    Bearish pin bar: long upper wick, small body near bottom.

    Returns:
        (bullish_pin, bearish_pin) boolean Series.
    """
    o, h, l, c = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"]
    bar_range = (h - l).replace(0, pd.NA)
    body = (c - o).abs()
    upper_wick = h - c.where(c >= o, o)
    lower_wick = c.where(c <= o, o) - l

    small_body = body / bar_range <= body_ratio_max

    bullish = small_body & (lower_wick >= wick_ratio * body) & (lower_wick > upper_wick)
    bearish = small_body & (upper_wick >= wick_ratio * body) & (upper_wick > lower_wick)

    return bullish.fillna(False), bearish.fillna(False)
