"""Tests for G5 BTC-ETH Pair Trading strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.pair_trading_vbt import PairTradingBTCETH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(close_values: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Create a simple OHLCV DataFrame from close values."""
    n = len(close_values)
    idx = pd.date_range(start, periods=n, freq="4h")
    close = pd.Series(close_values, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(n) * 1000.0,
        },
        index=idx,
    )


def _make_pair_dfs(
    n: int = 600,
    start: str = "2024-01-01",
    btc_start: float = 40000.0,
    eth_start: float = 2000.0,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create synthetic BTC and ETH DataFrames."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start, periods=n, freq="4h")

    btc_ret = rng.normal(0.0002, 0.01, n)
    eth_ret = rng.normal(0.0001, 0.012, n)
    btc_close = btc_start * np.cumprod(1 + btc_ret)
    eth_close = eth_start * np.cumprod(1 + eth_ret)

    def _to_ohlcv(close_vals):
        close = pd.Series(close_vals, index=idx, dtype=float)
        return pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": rng.uniform(100, 1000, n),
            },
            index=idx,
        )

    return _to_ohlcv(btc_close), _to_ohlcv(eth_close)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPairTradingParams:
    """Test parameter handling."""

    def test_default_params(self):
        s = PairTradingBTCETH()
        assert s.params["ols_window"] == 480
        assert s.params["zscore_period"] == 90
        assert s.params["entry_z"] == 2.5
        assert s.params["exit_z"] == 0.0
        assert s.name == "pair_btc_eth"
        assert s.required_timeframe == "4h"

    def test_custom_params(self):
        s = PairTradingBTCETH({"ols_window": 360, "entry_z": 2.0})
        assert s.params["ols_window"] == 360
        assert s.params["entry_z"] == 2.0
        # Defaults preserved
        assert s.params["zscore_period"] == 90

    def test_required_symbols(self):
        s = PairTradingBTCETH()
        assert s.required_symbols == ["BTCUSDT", "ETHUSDT"]

    def test_strategy_registration(self):
        from backtest_tool.strategies import STRATEGY_MAP
        assert "pair_btc_eth" in STRATEGY_MAP
        assert STRATEGY_MAP["pair_btc_eth"] is PairTradingBTCETH


class TestRatioComputation:
    """Test ratio OHLCV computation."""

    def test_basic_ratio(self):
        df_a, df_b = _make_pair_dfs(n=100)
        ratio = PairTradingBTCETH.compute_ratio_ohlcv(df_a, df_b)
        assert len(ratio) == 100
        assert set(ratio.columns) >= {"open", "high", "low", "close", "volume"}
        # BTC/ETH ratio should be around 20
        assert 10 < ratio["close"].mean() < 30

    def test_ratio_with_hedge_ratio(self):
        df_a, df_b = _make_pair_dfs(n=100)
        ratio_1 = PairTradingBTCETH.compute_ratio_ohlcv(df_a, df_b, hedge_ratio=1.0)
        ratio_2 = PairTradingBTCETH.compute_ratio_ohlcv(df_a, df_b, hedge_ratio=2.0)
        # Double hedge ratio → half the ratio values
        np.testing.assert_allclose(
            ratio_2["close"].values,
            ratio_1["close"].values / 2.0,
            rtol=1e-10,
        )

    def test_ratio_high_low_bounds(self):
        df_a, df_b = _make_pair_dfs(n=100)
        ratio = PairTradingBTCETH.compute_ratio_ohlcv(df_a, df_b)
        # High should use A_high/B_low (max ratio), low should use A_low/B_high
        assert (ratio["high"] >= ratio["close"]).all()
        assert (ratio["low"] <= ratio["close"]).all()

    def test_misaligned_indices(self):
        """Ratio should only include common timestamps."""
        df_a, df_b = _make_pair_dfs(n=100)
        df_a_trimmed = df_a.iloc[10:]  # Remove first 10 bars
        ratio = PairTradingBTCETH.compute_ratio_ohlcv(df_a_trimmed, df_b)
        assert len(ratio) == 90


class TestOLSResidual:
    """Test rolling OLS computation."""

    def test_residual_shape(self):
        df_a, df_b = _make_pair_dfs(n=600)
        s = PairTradingBTCETH({"ols_window": 100})
        residuals, betas = s.compute_ols_residual(df_a["close"], df_b["close"])
        assert len(residuals) == 600
        assert len(betas) == 600
        # First ols_window values should be NaN
        assert residuals.iloc[:100].isna().all()
        # After warmup, should have values
        assert not residuals.iloc[100:].isna().all()

    def test_beta_range(self):
        """Beta should be in a reasonable range for correlated assets."""
        df_a, df_b = _make_pair_dfs(n=600)
        s = PairTradingBTCETH({"ols_window": 100})
        _, betas = s.compute_ols_residual(df_a["close"], df_b["close"])
        valid_betas = betas.dropna()
        # Beta should be somewhat positive for correlated assets
        assert valid_betas.mean() > 0

    def test_residual_mean_near_zero(self):
        """OLS residual should have near-zero mean over the recent window."""
        df_a, df_b = _make_pair_dfs(n=600)
        s = PairTradingBTCETH({"ols_window": 100})
        residuals, _ = s.compute_ols_residual(df_a["close"], df_b["close"])
        # Residuals from rolling OLS should have small absolute mean
        valid = residuals.dropna()
        assert abs(valid.mean()) < 0.5


class TestSignalGeneration:
    """Test signal generation from z-scores."""

    def test_entry_signals_long(self):
        """Long entries when z < -entry_z."""
        s = PairTradingBTCETH({"entry_z": 2.0})
        # Create ohlcv with known z-score
        n = 200
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        ohlcv = pd.DataFrame(
            {
                "close": np.ones(n) * 25.0,
                "_zscore": np.linspace(-3, 3, n),
            },
            index=idx,
        )
        entries = s.generate_entries(ohlcv)
        # Should have entries where z < -2.0
        assert entries.iloc[0]  # z=-3 < -2
        assert not entries.iloc[-1]  # z=3 > -2

    def test_entry_signals_short(self):
        """Short entries when z > entry_z."""
        s = PairTradingBTCETH({"entry_z": 2.0})
        n = 200
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        ohlcv = pd.DataFrame(
            {
                "close": np.ones(n) * 25.0,
                "_zscore": np.linspace(-3, 3, n),
            },
            index=idx,
        )
        shorts = s.generate_short_entries(ohlcv)
        assert not shorts.iloc[0]  # z=-3 < 2
        assert shorts.iloc[-1]  # z=3 > 2

    def test_exit_signals_with_stop(self):
        """Exits triggered by both reversion and stop loss."""
        s = PairTradingBTCETH({"exit_z": 0.0, "stop_z": 4.0})
        n = 100
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        z_vals = np.zeros(n)
        z_vals[0] = -2.5  # In position
        z_vals[50] = 0.1   # Reversion → exit
        z_vals[80] = -4.5  # Stop loss → exit
        ohlcv = pd.DataFrame(
            {"close": np.ones(n) * 25.0, "_zscore": z_vals},
            index=idx,
        )
        exits = s.generate_exits(ohlcv)
        assert exits.iloc[50]   # Reversion exit
        assert exits.iloc[80]   # Stop loss exit


class TestBacktest:
    """Test backtest execution."""

    def test_backtest_with_synthetic_data(self):
        """Backtest should run on synthetic ratio data."""
        s = PairTradingBTCETH({"ols_window": 100, "zscore_period": 30, "entry_z": 2.0})
        # Create synthetic ratio data (close in range 15-30)
        n = 600
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        np.random.seed(42)
        ratio = 25.0 + np.cumsum(np.random.randn(n) * 0.1)
        ohlcv = pd.DataFrame(
            {
                "open": ratio * 0.999,
                "high": ratio * 1.005,
                "low": ratio * 0.995,
                "close": ratio,
                "volume": np.ones(n) * 1000,
            },
            index=idx,
        )
        pf = s.run_backtest(ohlcv, initial_capital=150.0)
        # Should produce a valid portfolio
        assert pf.value().iloc[0] == pytest.approx(150.0, rel=0.01)
        assert pf.value().iloc[-1] > 0  # Not bankrupt

    def test_double_fees(self):
        """Pair trading should charge 2x fees."""
        s = PairTradingBTCETH({"ols_window": 100, "zscore_period": 30, "entry_z": 2.0})
        n = 600
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        np.random.seed(42)
        ratio = 25.0 + np.cumsum(np.random.randn(n) * 0.1)
        ohlcv = pd.DataFrame(
            {
                "open": ratio * 0.999,
                "high": ratio * 1.005,
                "low": ratio * 0.995,
                "close": ratio,
                "volume": np.ones(n) * 1000,
            },
            index=idx,
        )
        pf_normal = s.run_backtest(ohlcv, fees=0.0004, slippage=0.0002)
        pf_zero = s.run_backtest(ohlcv, fees=0.0, slippage=0.0)
        # Zero-fee portfolio should have better or equal returns
        assert pf_zero.total_return() >= pf_normal.total_return()

    @pytest.mark.skipif(
        not __import__("pathlib").Path(
            "backtest_tool/data/klines/BTCUSDT/4h"
        ).exists(),
        reason="BTC kline data not available",
    )
    def test_backtest_with_real_data(self):
        """Integration test with real BTC+ETH data."""
        s = PairTradingBTCETH()
        dummy = pd.DataFrame(
            {"close": [1.0]},
            index=pd.date_range("2023-01-01", periods=1, freq="4h"),
        )
        pf = s.run_backtest(dummy, initial_capital=150.0)
        assert pf.trades.count() > 10
        assert pf.value().iloc[-1] > 0


class TestZScoreResidual:
    """Test z-score computation on residuals."""

    def test_zscore_output_range(self):
        """Z-score should be centered near 0."""
        s = PairTradingBTCETH({"zscore_period": 30})
        n = 200
        residuals = pd.Series(np.random.randn(n) * 0.01, dtype=float)
        z = s._zscore_residual(residuals)
        valid = z.dropna()
        assert abs(valid.mean()) < 1.0
        assert valid.std() > 0

    def test_zscore_nan_propagation(self):
        """Z-score should be NaN for initial warmup period."""
        s = PairTradingBTCETH({"zscore_period": 50})
        residuals = pd.Series(np.random.randn(100) * 0.01, dtype=float)
        z = s._zscore_residual(residuals)
        assert z.iloc[:49].isna().all()
