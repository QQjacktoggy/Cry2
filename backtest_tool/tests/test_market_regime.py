"""Tests for market regime detection and strategy selection."""

import numpy as np
import pandas as pd

from backtest_tool.engine.strategy_selector import StrategySelector
from backtest_tool.strategies.market_regime import REGIME_LABELS, MarketRegimeDetector


def _make_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    high = close + rng.uniform(50, 300, n)
    low = close - rng.uniform(50, 300, n)
    idx = pd.date_range("2021-01-01", periods=n, freq="4h")
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": rng.uniform(100, 1000, n)},
        index=idx,
    )


class TestMarketRegimeDetector:
    def setup_method(self):
        self.detector = MarketRegimeDetector()
        self.df = _make_ohlcv(500)

    def test_regime_output_shape(self):
        regime = self.detector.classify(self.df)
        assert len(regime) == len(self.df)

    def test_regime_labels_valid(self):
        regime = self.detector.classify(self.df)
        assert set(regime.unique()).issubset(set(REGIME_LABELS))

    def test_regime_stats_sums_to_one(self):
        stats = self.detector.regime_stats(self.df)
        total = sum(stats.values())
        assert abs(total - 1.0) < 1e-9

    def test_regime_requires_ohlcv_columns(self):
        """Works with a standard OHLCV DataFrame."""
        regime = self.detector.classify(self.df)
        assert isinstance(regime, pd.Series)
        assert regime.dtype == object  # string dtype


class TestStrategySelector:
    def setup_method(self):
        self.detector = MarketRegimeDetector()
        self.selector = StrategySelector(min_regime_bars=3)
        self.df = _make_ohlcv(500)
        self.regime = self.detector.classify(self.df)

    def test_strategy_selector_mask_shape(self):
        mask = self.selector.generate_entry_mask(self.regime, "trend_donchian")
        assert len(mask) == len(self.regime)

    def test_strategy_selector_trend_donchian_active_in_trending(self):
        """trend_donchian should be active in trending_up regime."""
        # Build a synthetic regime with all trending_up
        idx = pd.date_range("2021-01-01", periods=50, freq="4h")
        all_trending = pd.Series("trending_up", index=idx)
        mask = self.selector.generate_entry_mask(all_trending, "trend_donchian", smooth=False)
        assert mask.all(), "trend_donchian should be active for all trending_up bars"

    def test_strategy_selector_empty_in_volatile(self):
        """grid_futures should have no active bars when all regime is volatile."""
        idx = pd.date_range("2021-01-01", periods=50, freq="4h")
        all_volatile = pd.Series("volatile", index=idx)
        mask = self.selector.generate_entry_mask(all_volatile, "grid_futures", smooth=False)
        assert not mask.any(), "grid_futures should not trade in volatile regime"

    def test_smooth_regime_reduces_noise(self):
        """A short 1-bar regime change gets extended forward to min_regime_bars.

        The smoother prevents immediate re-entry of the prior regime by keeping
        the new regime alive for at least min_regime_bars bars.
        """
        idx = pd.date_range("2021-01-01", periods=20, freq="4h")
        # 10 bars trending_up, 1 bar ranging, 9 bars trending_up
        labels = ["trending_up"] * 10 + ["ranging"] + ["trending_up"] * 9
        regime = pd.Series(labels, index=idx)
        selector = StrategySelector(min_regime_bars=3)
        smoothed = selector.smooth_regime(regime)
        # Bar 10: "ranging" (original; accepted because preceding A-run was long)
        # Bar 11: "ranging" (extended from original "trending_up" — B run < min_regime_bars)
        # Bar 12+: "trending_up" (accepted after B run reaches min_regime_bars at i=13)
        assert smoothed.iloc[10] == "ranging"
        assert smoothed.iloc[11] == "ranging"
        assert smoothed.iloc[12] == "trending_up"
