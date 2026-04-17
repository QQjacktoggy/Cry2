"""Volume-based indicators for signal filtering.

Provides volume filter (low liquidity → skip entry) and
volume MA ratio for confirming breakouts.
"""

from __future__ import annotations

import pandas as pd


def compute_volume_filter(
    volume: pd.Series,
    lookback: int = 20,
    percentile: float = 20.0,
) -> pd.Series:
    """Return True when volume is above the given percentile (liquid enough to trade).

    Bars below the percentile threshold are filtered out (returns False).

    Args:
        volume: Volume Series.
        lookback: Rolling window for percentile calculation.
        percentile: Minimum volume percentile (0-100) to allow trading.

    Returns:
        Boolean Series (True = volume is adequate).
    """
    rolling_pct = volume.rolling(lookback).apply(
        lambda x: (x[-1] >= x).mean() * 100, raw=True
    )
    return (rolling_pct >= percentile).fillna(False)


def compute_volume_ma_ratio(
    volume: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Compute ratio of current volume to rolling average volume.

    Useful for confirming breakouts (volume spike > 1.5× average).

    Args:
        volume: Volume Series.
        window: Rolling window for average volume.

    Returns:
        Series of volume / rolling_average (>1 = above average).
    """
    vol_ma = volume.rolling(window).mean()
    ratio = volume / vol_ma
    return ratio.fillna(1.0)
