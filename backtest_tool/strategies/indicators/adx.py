"""ADX (Average Directional Index) indicator using ta library."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator


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
    adx_indicator = ADXIndicator(high=high, low=low, close=close, window=period)
    return adx_indicator.adx()
