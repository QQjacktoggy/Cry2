"""Bollinger Bands, RSI, and EMA indicators — pure pandas/numpy implementation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_bollinger(
    close: pd.Series,
    period: int = 20,
    std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Bollinger Bands.

    Args:
        close: Close price series.
        period: SMA period.
        std: Number of standard deviations.

    Returns:
        Tuple of (upper, middle, lower) Series.
    """
    middle = close.rolling(window=period, min_periods=period).mean()
    rolling_std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std * rolling_std
    lower = middle - std * rolling_std
    return upper, middle, lower


def compute_rsi(
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute RSI (Relative Strength Index).

    Args:
        close: Close price series.
        period: RSI period.

    Returns:
        RSI Series (0-100, NaN for warmup bars).
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_ema(
    close: pd.Series,
    period: int = 200,
) -> pd.Series:
    """Compute Exponential Moving Average.

    Args:
        close: Close price series.
        period: EMA period.

    Returns:
        EMA Series.
    """
    return close.ewm(span=period, adjust=False).mean()
