"""ATR (Average True Range) indicator using ta library."""

from __future__ import annotations

import pandas as pd
from ta.volatility import AverageTrueRange


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute ATR Series.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ATR period.

    Returns:
        ATR Series (NaN for warmup bars).
    """
    atr_indicator = AverageTrueRange(high=high, low=low, close=close, window=period)
    return atr_indicator.average_true_range()


def compute_atr_percentile(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_period: int = 14,
    lookback: int = 100,
    percentile: float = 50.0,
) -> pd.Series:
    """Compute whether current ATR exceeds a rolling percentile.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        atr_period: ATR calculation period.
        lookback: Rolling window for percentile calculation.
        percentile: Percentile threshold (0-100).

    Returns:
        Boolean Series (True = ATR above percentile threshold).
    """
    atr = compute_atr(high, low, close, atr_period)
    rolling_pct = atr.rolling(lookback).quantile(percentile / 100.0)
    return atr >= rolling_pct
