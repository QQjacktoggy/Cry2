"""Donchian Channel indicator using pandas."""

from __future__ import annotations

import pandas as pd


def compute_donchian(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Donchian Channel.

    Upper = highest high over period (shifted by 1 to avoid look-ahead).
    Lower = lowest low over period (shifted by 1 to avoid look-ahead).
    Middle = (upper + lower) / 2.

    Args:
        high: High price series.
        low: Low price series.
        period: Lookback period.

    Returns:
        Tuple of (upper, lower, middle) Series.
        All shifted by 1 bar to prevent look-ahead bias.
    """
    upper = high.rolling(window=period).max().shift(1)
    lower = low.rolling(window=period).min().shift(1)
    middle = (upper + lower) / 2.0
    return upper, lower, middle
