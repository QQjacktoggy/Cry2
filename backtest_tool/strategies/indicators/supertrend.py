"""Supertrend indicator."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_tool.strategies.indicators.atr import compute_atr


def compute_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Compute Supertrend.

    Returns:
        (supertrend, direction). direction=1 (bullish) or -1 (bearish).
    """
    atr = compute_atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    n = len(close)
    st = np.zeros(n)
    dir_ = np.ones(n, dtype=int)

    u = upper.to_numpy()
    l = lower.to_numpy()
    c = close.to_numpy()

    st[0] = u[0]
    for i in range(1, n):
        # Final upper band
        if u[i] < st[i - 1] or c[i - 1] > st[i - 1]:
            final_u = u[i]
        else:
            final_u = st[i - 1]
        # Final lower band
        if l[i] > st[i - 1] or c[i - 1] < st[i - 1]:
            final_l = l[i]
        else:
            final_l = st[i - 1]

        if dir_[i - 1] == 1:
            if c[i] < final_l:
                dir_[i] = -1
                st[i] = final_u
            else:
                dir_[i] = 1
                st[i] = final_l
        else:
            if c[i] > final_u:
                dir_[i] = 1
                st[i] = final_l
            else:
                dir_[i] = -1
                st[i] = final_u

    return (
        pd.Series(st, index=close.index),
        pd.Series(dir_, index=close.index),
    )
