"""Tests for DataValidator."""

import numpy as np
import pandas as pd

from backtest_tool.data_manager.validator import DataValidator


def _make_valid_kline_df(n: int = 100) -> pd.DataFrame:
    """Create valid kline DataFrame."""
    start_ts = 1672531200000  # 2023-01-01
    interval = 14400000  # 4h
    timestamps = [start_ts + i * interval for i in range(n)]
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 50, n))
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close + rng.normal(0, 20, n),
        "high": close + rng.uniform(30, 100, n),
        "low": close - rng.uniform(30, 100, n),
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


class TestDataValidator:
    def setup_method(self):
        self.validator = DataValidator()

    def test_valid_data_passes(self):
        df = _make_valid_kline_df()
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert result.passed is True
        assert len(result.errors) == 0

    def test_non_monotonic_timestamp(self):
        df = _make_valid_kline_df()
        # Swap two timestamps
        df.loc[5, "timestamp"] = df.loc[3, "timestamp"]
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert result.passed is False
        assert any("monotonically" in e for e in result.errors)

    def test_negative_price_fails(self):
        df = _make_valid_kline_df()
        df.loc[10, "close"] = -100
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert result.passed is False

    def test_negative_volume_fails(self):
        df = _make_valid_kline_df()
        df.loc[5, "volume"] = -10
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert result.passed is False

    def test_gap_detection(self):
        df = _make_valid_kline_df(50)
        # Create a gap by removing rows 20-30
        df = pd.concat([df.iloc[:20], df.iloc[30:]]).reset_index(drop=True)
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert result.missing_bars > 0

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        result = self.validator.validate_klines(df, "BTCUSDT", "4h")
        assert len(result.warnings) > 0

    def test_funding_validation(self):
        timestamps = [1672531200000 + i * 28800000 for i in range(30)]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "funding_rate": np.random.default_rng(42).normal(0.0001, 0.0005, 30),
        })
        result = self.validator.validate_funding(df, "BTCUSDT")
        assert result.passed is True
