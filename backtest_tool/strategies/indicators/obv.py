"""OBV (On-Balance Volume) and MFI (Money Flow Index) indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Compute On-Balance Volume."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def compute_mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Money Flow Index (0-100)."""
    typical = (high + low + close) / 3.0
    raw_mf = typical * volume
    flow_dir = typical.diff()
    pos_flow = raw_mf.where(flow_dir > 0, 0.0).rolling(period).sum()
    neg_flow = raw_mf.where(flow_dir <= 0, 0.0).rolling(period).sum()
    mfi = 100.0 - 100.0 / (1.0 + pos_flow / neg_flow.replace(0, np.nan))
    return mfi.fillna(50.0)
