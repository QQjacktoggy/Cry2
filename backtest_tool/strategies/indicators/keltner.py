"""Keltner Channel indicator."""

from __future__ import annotations

import pandas as pd

from backtest_tool.strategies.indicators.atr import compute_atr
from backtest_tool.strategies.indicators.bollinger import compute_ema


def compute_keltner(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_period: int = 20,
    atr_period: int = 10,
    atr_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Keltner Channel (upper, middle, lower)."""
    middle = compute_ema(close, ema_period)
    atr = compute_atr(high, low, close, atr_period)
    upper = middle + atr_mult * atr
    lower = middle - atr_mult * atr
    return upper, middle, lower
