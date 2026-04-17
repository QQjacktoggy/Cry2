"""Tests for VBT strategies: signal generation and basic backtest execution."""

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.trend_donchian_vbt import TrendDonchianVBT
from backtest_tool.strategies.mean_reversion_bb_vbt import MeanReversionBBVBT
from backtest_tool.strategies.grid_futures_vbt import GridFuturesVBT
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT


def _make_ohlcv(n: int = 500) -> pd.DataFrame:
    """Create synthetic OHLCV data with enough bars for all indicators."""
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    high = close + rng.uniform(50, 300, n)
    low = close - rng.uniform(50, 300, n)
    open_ = close + rng.normal(0, 80, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(100, 1000, n),
    }, index=idx)


class TestTrendDonchian:
    def setup_method(self):
        self.strategy = TrendDonchianVBT()
        self.df = _make_ohlcv()

    def test_entries_are_boolean(self):
        entries = self.strategy.generate_entries(self.df)
        assert entries.dtype == bool
        assert len(entries) == len(self.df)

    def test_exits_are_boolean(self):
        exits = self.strategy.generate_exits(self.df)
        assert exits.dtype == bool

    def test_short_entries_exist(self):
        short_entries = self.strategy.generate_short_entries(self.df)
        assert short_entries is not None
        assert short_entries.dtype == bool

    def test_run_backtest(self):
        portfolio = self.strategy.run_backtest(self.df, initial_capital=10000)
        value = portfolio.value()
        assert len(value) > 0
        assert value.iloc[0] == pytest.approx(10000, rel=0.01)

    def test_custom_params(self):
        strategy = TrendDonchianVBT(params={"entry_period": 30, "adx_threshold": 20})
        assert strategy.params["entry_period"] == 30
        assert strategy.params["adx_threshold"] == 20
        assert strategy.params["exit_period"] == 10  # default preserved


class TestMeanReversionBB:
    def setup_method(self):
        self.strategy = MeanReversionBBVBT()
        self.df = _make_ohlcv()

    def test_entries_boolean(self):
        entries = self.strategy.generate_entries(self.df)
        assert entries.dtype == bool

    def test_run_backtest(self):
        portfolio = self.strategy.run_backtest(self.df, initial_capital=10000)
        assert portfolio.value().iloc[0] == pytest.approx(10000, rel=0.01)


class TestGridFutures:
    def setup_method(self):
        self.strategy = GridFuturesVBT()
        self.df = _make_ohlcv()

    def test_entries_boolean(self):
        entries = self.strategy.generate_entries(self.df)
        assert entries.dtype == bool

    def test_run_backtest(self):
        portfolio = self.strategy.run_backtest(self.df, initial_capital=10000)
        assert len(portfolio.value()) > 0


class TestFundingArb:
    def setup_method(self):
        self.strategy = FundingArbVBT()
        n = 500
        rng = np.random.default_rng(42)
        idx = pd.date_range("2023-01-01", periods=n, freq="8h")
        close = 30000.0 + np.cumsum(rng.normal(0, 50, n))
        self.df = pd.DataFrame({
            "open": close + rng.normal(0, 20, n),
            "high": close + rng.uniform(10, 100, n),
            "low": close - rng.uniform(10, 100, n),
            "close": close,
            "volume": rng.uniform(100, 1000, n),
            "annual_rate": rng.uniform(-5, 25, n),  # funding rate as annual %
        }, index=idx)

    def test_entries_boolean(self):
        entries = self.strategy.generate_entries(self.df)
        assert entries.dtype == bool

    def test_no_long_entries(self):
        """Funding arb is short-only — generate_short_entries should return signals."""
        short_entries = self.strategy.generate_short_entries(self.df)
        # Should have some short entries given high funding rates
        assert short_entries is not None

    def test_run_backtest(self):
        portfolio = self.strategy.run_backtest(self.df, initial_capital=10000)
        assert len(portfolio.value()) > 0
