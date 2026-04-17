"""Stochastic Oscillator and Rate of Change indicators."""

from __future__ import annotations

import pandas as pd


def compute_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
    smooth_k: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Compute Stochastic %K and %D."""
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    k_raw = 100.0 * (close - lowest) / (highest - lowest).replace(0, pd.NA)
    k = k_raw.rolling(smooth_k).mean()
    d = k.rolling(d_period).mean()
    return k.fillna(50.0), d.fillna(50.0)


def compute_roc(close: pd.Series, period: int = 20) -> pd.Series:
    """Rate of Change (%)."""
    return (close / close.shift(period) - 1.0) * 100.0
