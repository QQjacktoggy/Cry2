"""Ichimoku Cloud indicator."""

from __future__ import annotations

import pandas as pd


def compute_ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> dict[str, pd.Series]:
    """Compute Ichimoku Cloud components.

    Returns:
        dict with tenkan, kijun, senkou_a, senkou_b, chikou.
    """
    tenkan = (high.rolling(tenkan_period).max() + low.rolling(tenkan_period).min()) / 2.0
    kijun = (high.rolling(kijun_period).max() + low.rolling(kijun_period).min()) / 2.0
    senkou_a = ((tenkan + kijun) / 2.0).shift(displacement)
    senkou_b = (
        (high.rolling(senkou_b_period).max() + low.rolling(senkou_b_period).min()) / 2.0
    ).shift(displacement)
    chikou = close.shift(-displacement)

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": chikou,
    }
