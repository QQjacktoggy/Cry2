"""Bollinger Bands, RSI, and EMA indicators using ta library."""

from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import BollingerBands


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
    bb = BollingerBands(close=close, window=period, window_dev=std)
    upper = bb.bollinger_hband()
    middle = bb.bollinger_mavg()
    lower = bb.bollinger_lband()
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
    rsi = RSIIndicator(close=close, window=period)
    return rsi.rsi()


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
    ema = EMAIndicator(close=close, window=period)
    return ema.ema_indicator()
