"""ATR (Average True Range) indicator — pure pandas/numpy implementation."""

from __future__ import annotations

import pandas as pd


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
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


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
