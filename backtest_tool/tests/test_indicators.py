"""Tests for indicator functions."""

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.indicators.atr import compute_atr, compute_atr_percentile
from backtest_tool.strategies.indicators.adx import compute_adx
from backtest_tool.strategies.indicators.bollinger import compute_bollinger, compute_rsi, compute_ema
from backtest_tool.strategies.indicators.donchian import compute_donchian


def _make_ohlcv(n: int = 200) -> pd.DataFrame:
    """Create synthetic OHLCV data."""
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    high = close + rng.uniform(50, 200, n)
    low = close - rng.uniform(50, 200, n)
    open_ = close + rng.normal(0, 50, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(100, 1000, n),
    }, index=idx)


class TestIndicators:
    def setup_method(self):
        self.df = _make_ohlcv()

    def test_atr_shape_and_nans(self):
        atr = compute_atr(self.df["high"], self.df["low"], self.df["close"], period=14)
        assert len(atr) == len(self.df)
        assert atr.iloc[-1] > 0  # Should have valid values after warmup

    def test_atr_percentile_boolean(self):
        result = compute_atr_percentile(self.df["high"], self.df["low"], self.df["close"])
        valid = result.dropna()
        assert valid.dtype == bool

    def test_adx_range(self):
        adx = compute_adx(self.df["high"], self.df["low"], self.df["close"], period=14)
        valid = adx.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_bollinger_three_bands(self):
        upper, middle, lower = compute_bollinger(self.df["close"], period=20, std=2.0)
        valid_mask = upper.notna() & middle.notna() & lower.notna()
        assert valid_mask.sum() > 0
        # Upper > middle > lower
        assert (upper[valid_mask] >= middle[valid_mask]).all()
        assert (middle[valid_mask] >= lower[valid_mask]).all()

    def test_rsi_range(self):
        rsi = compute_rsi(self.df["close"], period=14)
        valid = rsi.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_ema_length(self):
        ema = compute_ema(self.df["close"], period=20)
        assert len(ema) == len(self.df)

    def test_donchian_shift(self):
        upper, lower, middle = compute_donchian(self.df["high"], self.df["low"], period=20)
        # First 20 bars should be NaN due to rolling + shift(1)
        assert upper.iloc[0] != upper.iloc[0]  # NaN check
        # After warmup, upper should be max of previous N highs
        assert upper.dropna().iloc[0] > 0

    def test_donchian_no_lookahead(self):
        """Verify Donchian channel uses shift(1) — no look-ahead bias."""
        upper, lower, middle = compute_donchian(self.df["high"], self.df["low"], period=5)
        # At index 6, upper should be max of high[1:6] (not including index 6)
        idx = 6
        expected_upper = self.df["high"].iloc[1:6].max()
        assert abs(upper.iloc[idx] - expected_upper) < 1e-6
