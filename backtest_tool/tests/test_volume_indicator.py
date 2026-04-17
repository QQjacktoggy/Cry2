"""Tests for volume indicator functions."""

import numpy as np
import pandas as pd

from backtest_tool.strategies.indicators.volume import (
    compute_volume_filter,
    compute_volume_ma_ratio,
)


def _make_volume(n: int = 300, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="4h")
    return pd.Series(rng.uniform(100, 1000, n), index=idx)


class TestVolumeFilter:
    def setup_method(self):
        self.volume = _make_volume(300)

    def test_volume_filter_shape(self):
        result = compute_volume_filter(self.volume)
        assert len(result) == len(self.volume)

    def test_volume_filter_bool_type(self):
        result = compute_volume_filter(self.volume)
        assert result.dtype == bool

    def test_volume_filter_low_liquidity(self):
        """First `lookback` bars may be False due to rolling NaN period."""
        lookback = 20
        result = compute_volume_filter(self.volume, lookback=lookback)
        # The first lookback-1 bars should be False (NaN → fillna(False))
        assert not result.iloc[:lookback - 1].any()


class TestVolumeMaRatio:
    def setup_method(self):
        self.volume = _make_volume(300)

    def test_volume_ma_ratio_shape(self):
        result = compute_volume_ma_ratio(self.volume)
        assert len(result) == len(self.volume)

    def test_volume_ma_ratio_mean_around_one(self):
        """Over a long series, ratio mean should be near 1.0."""
        result = compute_volume_ma_ratio(self.volume, window=20)
        # After the warmup period, ratios average ~1
        mean_ratio = result.iloc[20:].mean()
        assert abs(mean_ratio - 1.0) < 0.5
