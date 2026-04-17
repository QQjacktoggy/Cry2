"""Tests for DataStore: Parquet CRUD operations."""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest_tool.data_manager.store import DataStore, KLINE_COLUMNS


def _make_kline_df(n: int = 100, start_ts: int = 1672531200000, interval_ms: int = 14400000) -> pd.DataFrame:
    """Create synthetic kline DataFrame."""
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    high = close + rng.uniform(50, 200, n)
    low = close - rng.uniform(50, 200, n)
    open_ = close + rng.normal(0, 50, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(100, 1000, n),
        "quote_volume": rng.uniform(3e6, 3e7, n),
        "trade_count": rng.integers(1000, 10000, n),
        "taker_buy_volume": rng.uniform(50, 500, n),
        "taker_buy_quote_volume": rng.uniform(1e6, 1e7, n),
    })


class TestDataStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = DataStore(data_dir=self.tmpdir)

    def test_save_and_load_klines(self):
        df = _make_kline_df(50)
        self.store.save_klines(df, "BTCUSDT", "4h")
        loaded = self.store.load_klines("BTCUSDT", "4h")
        assert len(loaded) == 50
        assert "close" in loaded.columns
        assert loaded.index.name == "datetime"

    def test_dedup_on_save(self):
        df = _make_kline_df(50)
        self.store.save_klines(df, "BTCUSDT", "4h")
        # Save same data again
        self.store.save_klines(df, "BTCUSDT", "4h")
        loaded = self.store.load_klines("BTCUSDT", "4h")
        assert len(loaded) == 50  # No duplicates

    def test_date_filtering(self):
        # Create data spanning 2023-01-01 to ~2023-01-25 (4h bars)
        df = _make_kline_df(150, start_ts=1672531200000)  # 2023-01-01
        self.store.save_klines(df, "BTCUSDT", "4h")

        loaded = self.store.load_klines("BTCUSDT", "4h", start="2023-01-10", end="2023-01-20")
        assert len(loaded) > 0
        assert loaded.index[0] >= pd.Timestamp("2023-01-10")
        assert loaded.index[-1] <= pd.Timestamp("2023-01-21")

    def test_empty_load(self):
        loaded = self.store.load_klines("NONEXISTENT", "1h")
        assert loaded.empty

    def test_save_and_load_funding(self):
        timestamps = [1672531200000 + i * 28800000 for i in range(30)]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "symbol": "BTCUSDT",
            "funding_rate": np.random.default_rng(42).normal(0.0001, 0.0005, 30),
        })
        self.store.save_funding(df, "BTCUSDT")
        loaded = self.store.load_funding("BTCUSDT")
        assert len(loaded) == 30
        assert "funding_rate" in loaded.columns

    def test_list_available(self):
        df = _make_kline_df(50)
        self.store.save_klines(df, "BTCUSDT", "4h")
        available = self.store.list_available()
        assert len(available["klines"]) == 1
        assert available["klines"][0]["symbol"] == "BTCUSDT"

    def test_missing_column_raises(self):
        df = pd.DataFrame({"timestamp": [1], "open": [100]})
        with pytest.raises(ValueError, match="Missing required column"):
            self.store.save_klines(df, "BTCUSDT", "4h")
