"""Z-score and VWAP indicators."""

from __future__ import annotations

import pandas as pd


def compute_zscore(close: pd.Series, period: int = 20) -> pd.Series:
    """Rolling z-score of close vs its rolling mean/std."""
    mean = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return (close - mean) / std.replace(0, pd.NA)


def compute_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """Rolling VWAP with rolling standard deviation.

    Returns:
        (vwap, std).
    """
    typical = (high + low + close) / 3.0
    tpv = typical * volume
    vwap = tpv.rolling(period).sum() / volume.rolling(period).sum().replace(0, pd.NA)
    dev = ((typical - vwap) ** 2) * volume
    var = dev.rolling(period).sum() / volume.rolling(period).sum().replace(0, pd.NA)
    std = var.pow(0.5)
    return vwap, std
