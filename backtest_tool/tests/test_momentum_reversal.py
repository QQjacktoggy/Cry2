"""Tests for MomentumReversalVBT strategy."""

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.momentum_reversal_vbt import MomentumReversalVBT
from backtest_tool.strategies import STRATEGY_MAP


def _make_ohlcv(n: int = 450) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 200, n))
    high = close + rng.uniform(50, 300, n)
    low = close - rng.uniform(50, 300, n)
    idx = pd.date_range("2021-01-01", periods=n, freq="1D")
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": rng.uniform(100, 1000, n)},
        index=idx,
    )


class TestMomentumReversalVBT:
    def setup_method(self):
        self.strategy = MomentumReversalVBT()
        self.df = _make_ohlcv()

    def test_default_params(self):
        params = self.strategy.params
        assert "lookback" in params
        assert "rsi_period" in params
        assert "rsi_oversold" in params
        assert "rsi_overbought" in params
        assert "return_threshold" in params

    def test_generate_entries_type(self):
        entries = self.strategy.generate_entries(self.df)
        assert entries.dtype == bool

    def test_generate_entries_length(self):
        entries = self.strategy.generate_entries(self.df)
        assert len(entries) == len(self.df)

    def test_generate_exits_type(self):
        exits = self.strategy.generate_exits(self.df)
        assert exits.dtype == bool

    def test_short_entries_type(self):
        short_entries = self.strategy.generate_short_entries(self.df)
        assert short_entries.dtype == bool

    def test_short_exits_type(self):
        short_exits = self.strategy.generate_short_exits(self.df)
        assert short_exits.dtype == bool

    def test_run_backtest_returns_portfolio(self):
        import vectorbt as vbt
        portfolio = self.strategy.run_backtest(self.df, initial_capital=10000)
        assert isinstance(portfolio, vbt.Portfolio)

    def test_in_strategy_map(self):
        assert "momentum_reversal" in STRATEGY_MAP
        assert STRATEGY_MAP["momentum_reversal"] is MomentumReversalVBT
