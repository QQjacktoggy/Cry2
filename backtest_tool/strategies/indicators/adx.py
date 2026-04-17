"""ADX (Average Directional Index) indicator — pure pandas/numpy implementation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute ADX (Average Directional Index).

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ADX period.

    Returns:
        ADX Series (0-100, NaN for warmup bars).
    """
    high = high.copy()
    low = low.copy()
    close = close.copy()

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Directional Movement
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=high.index, dtype=float)
    minus_dm_s = pd.Series(minus_dm, index=high.index, dtype=float)

    # Smoothed values (Wilder EMA = equivalent of rolling sum with alpha=1/period)
    alpha = 1.0 / period
    atr_s = tr.ewm(alpha=alpha, adjust=False).mean() * period
    plus_di = 100.0 * (plus_dm_s.ewm(alpha=alpha, adjust=False).mean() * period) / atr_s.replace(0, np.nan)
    minus_di = 100.0 * (minus_dm_s.ewm(alpha=alpha, adjust=False).mean() * period) / atr_s.replace(0, np.nan)

    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx.fillna(0.0)
