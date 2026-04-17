"""Tests for BacktestRunner, ParamScanner, and CostModel."""

import numpy as np
import pandas as pd
import pytest

from backtest_tool.engine.cost_model import CostModel
from backtest_tool.engine.runner import BacktestRunner, BacktestResult
from backtest_tool.engine.param_scanner import ParamScanner
from backtest_tool.strategies.trend_donchian_vbt import TrendDonchianVBT


def _make_ohlcv(n: int = 500) -> pd.DataFrame:
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


class TestCostModel:
    def test_defaults(self):
        cm = CostModel()
        assert cm.taker_rate == 0.0004
        assert cm.maker_rate == 0.0002
        assert cm.slippage_bps == 2.0

    def test_total_cost_rate(self):
        cm = CostModel(taker_rate=0.0004, slippage_bps=2.0)
        expected = 0.0004 + 0.0002  # taker + 2bps
        assert cm.total_cost_rate == pytest.approx(expected)

    def test_slippage_rate(self):
        cm = CostModel(slippage_bps=5.0)
        assert cm.slippage_rate == pytest.approx(0.0005)

    def test_summary(self):
        cm = CostModel()
        s = cm.summary()
        assert "maker_rate" in s
        assert "total_cost_rate" in s


class TestBacktestRunner:
    def setup_method(self):
        self.runner = BacktestRunner()
        self.df = _make_ohlcv()

    def test_run_single_returns_result(self):
        strategy = TrendDonchianVBT()
        result = self.runner.run_single(strategy, self.df, symbol="BTCUSDT", timeframe="4h")
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "trend_donchian"
        assert result.symbol == "BTCUSDT"
        assert result.timeframe == "4h"

    def test_metrics_keys(self):
        strategy = TrendDonchianVBT()
        result = self.runner.run_single(strategy, self.df)
        m = result.metrics
        assert "total_return" in m
        assert "sharpe_ratio" in m
        assert "max_drawdown" in m
        assert "total_trades" in m

    def test_equity_curve_starts_at_capital(self):
        strategy = TrendDonchianVBT()
        result = self.runner.run_single(strategy, self.df, initial_capital=50000)
        assert result.equity_curve.iloc[0] == pytest.approx(50000, rel=0.01)


class TestParamScanner:
    def setup_method(self):
        self.runner = BacktestRunner()
        self.df = _make_ohlcv()

    def test_scan_returns_dataframe(self):
        scanner = ParamScanner(self.runner)
        param_space = {"entry_period": [15, 20], "exit_period": [10]}
        results = scanner.scan(TrendDonchianVBT, param_space, self.df, top_n=5)
        assert isinstance(results, pd.DataFrame)
        assert len(results) <= 5
        assert "sharpe_ratio" in results.columns

    def test_empty_param_space(self):
        scanner = ParamScanner(self.runner)
        results = scanner.scan(TrendDonchianVBT, {}, self.df)
        assert isinstance(results, pd.DataFrame)
        assert len(results) >= 1  # At least default params
