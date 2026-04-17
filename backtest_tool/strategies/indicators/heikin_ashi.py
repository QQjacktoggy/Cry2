"""Heikin-Ashi candle computation."""

from __future__ import annotations

import pandas as pd


def compute_heikin_ashi(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Convert OHLC to Heikin-Ashi OHLC.

    Returns:
        DataFrame with ha_open, ha_high, ha_low, ha_close columns.
    """
    ha_close = (ohlcv["open"] + ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 4.0

    ha_open = pd.Series(index=ohlcv.index, dtype=float)
    ha_open.iloc[0] = (ohlcv["open"].iloc[0] + ohlcv["close"].iloc[0]) / 2.0
    for i in range(1, len(ohlcv)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha_high = pd.concat([ohlcv["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([ohlcv["low"], ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame(
        {
            "ha_open": ha_open,
            "ha_high": ha_high,
            "ha_low": ha_low,
            "ha_close": ha_close,
        }
    )
