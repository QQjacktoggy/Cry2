"""Tests for WalkForwardAnalyzer and MonteCarloSimulator."""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock

from backtest_tool.engine.walk_forward import WalkForwardAnalyzer, WalkForwardResult
from backtest_tool.engine.monte_carlo import MonteCarloSimulator, MonteCarloResult


def _make_3yr_index() -> pd.DatetimeIndex:
    """3 years of daily bars."""
    return pd.date_range("2020-01-01", "2023-01-01", freq="D")


def _make_mock_result(n_trades: int = 20, seed: int = 42) -> MagicMock:
    rng = np.random.default_rng(seed)
    mock_result = MagicMock()
    mock_result.trades = pd.DataFrame({"PnL": rng.normal(0, 10, n_trades)})
    mock_result.equity_curve = pd.Series(
        [10000.0 + i * 10 for i in range(100)],
        index=pd.date_range("2020-01-01", periods=100, freq="D"),
    )
    mock_result.metrics = {"initial_capital": 10000.0, "max_drawdown": -0.05}
    return mock_result


class TestWalkForwardAnalyzer:
    def setup_method(self):
        self.wfa = WalkForwardAnalyzer(
            train_months=12, test_months=3, step_months=3
        )

    def test_walk_forward_generate_windows(self):
        idx = _make_3yr_index()
        windows = self.wfa._generate_windows(idx)
        assert isinstance(windows, list)
        assert len(windows) > 0
        for w in windows:
            assert len(w) == 4  # (train_start, train_end, test_start, test_end)

    def test_walk_forward_window_count(self):
        """3 years, 12-month train + 3-month test + 3-month step → ~8 windows."""
        idx = _make_3yr_index()
        windows = self.wfa._generate_windows(idx)
        assert 7 <= len(windows) <= 9

    def test_walk_forward_result_fields(self):
        result = WalkForwardResult()
        assert hasattr(result, "windows")
        assert hasattr(result, "train_sharpe_mean")
        assert hasattr(result, "test_sharpe_mean")
        assert hasattr(result, "sharpe_degradation")
        assert hasattr(result, "oos_sharpe_positive")
        assert hasattr(result, "summary")

    def test_walk_forward_run_returns_result(self):
        """run() with minimal mock scanner returns a WalkForwardResult."""
        from backtest_tool.strategies.trend_donchian_vbt import TrendDonchianVBT

        rng = np.random.default_rng(42)
        n = 200
        close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
        ohlcv = pd.DataFrame(
            {
                "open": close,
                "high": close + rng.uniform(50, 200, n),
                "low": close - rng.uniform(50, 200, n),
                "close": close,
                "volume": rng.uniform(100, 1000, n),
            },
            index=pd.date_range("2020-01-01", periods=n, freq="D"),
        )

        mock_scanner = MagicMock()
        mock_scanner.scan.return_value = []  # no results → skips windows

        result = self.wfa.run(
            strategy_cls=TrendDonchianVBT,
            ohlcv=ohlcv,
            param_space={"entry_period": [20]},
            scanner=mock_scanner,
        )
        assert isinstance(result, WalkForwardResult)


class TestMonteCarloSimulator:
    def test_monte_carlo_empty_trades(self):
        mock_result = MagicMock()
        mock_result.trades = pd.DataFrame()
        sim = MonteCarloSimulator(n_simulations=100)
        result = sim.run(mock_result)
        assert isinstance(result, MonteCarloResult)
        assert result.n_simulations == 0

    def test_monte_carlo_shape(self):
        """With 20 trades, final_values has shape (n_simulations,)."""
        mock_result = _make_mock_result(n_trades=20)
        sim = MonteCarloSimulator(n_simulations=200, seed=42)
        result = sim.run(mock_result)
        assert result.n_simulations == 200
        assert result.final_values.shape == (200,)

    def test_monte_carlo_percentiles_present(self):
        mock_result = _make_mock_result(n_trades=20)
        sim = MonteCarloSimulator(n_simulations=100, seed=42)
        result = sim.run(mock_result)
        assert "final_value_p50" in result.percentiles
        assert "max_dd_p50" in result.percentiles

    def test_monte_carlo_reproducible(self):
        """Same seed → same final_values."""
        mock_result = _make_mock_result(n_trades=20, seed=99)
        result1 = MonteCarloSimulator(n_simulations=100, seed=7).run(mock_result)
        result2 = MonteCarloSimulator(n_simulations=100, seed=7).run(mock_result)
        np.testing.assert_array_equal(result1.final_values, result2.final_values)
