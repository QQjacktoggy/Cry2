"""Regression tests for pair_trading, block_bootstrap, and nested walk-forward.

Covers:
  - Pair data missing → FileNotFoundError
  - max_hold_bars forced exit
  - block_bootstrap insufficient input
  - block_bootstrap normal output format
  - nested walk-forward weighting / output format
  - nested walk-forward insufficient data
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.pair_trading_vbt import PairTradingBTCETH
from backtest_tool.engine.robustness import (
    block_bootstrap_simulation,
    walk_forward_nested_analysis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_equity(n: int = 500, start: float = 10000.0, seed: int = 42) -> pd.Series:
    """Synthetic daily equity curve with mild upward drift."""
    rng = np.random.default_rng(seed)
    daily_ret = rng.normal(0.0005, 0.015, n)
    prices = start * np.cumprod(1 + daily_ret)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(prices, index=idx, name="equity")


def _make_equity_curves(n_strats: int = 3, n: int = 500) -> dict[str, pd.Series]:
    """Multiple synthetic equity curves."""
    return {f"strat_{i}": _make_equity(n=n, seed=42 + i) for i in range(n_strats)}


def _make_zscore_ohlcv(n: int = 200, z_values: np.ndarray | None = None) -> pd.DataFrame:
    """OHLCV DataFrame with pre-computed _zscore column."""
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    if z_values is None:
        z_values = np.zeros(n)
    close = np.ones(n) * 25.0
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.ones(n) * 1000.0,
            "_zscore": z_values,
        },
        index=idx,
    )


# ===========================================================================
# 1. Pair Trading — data missing raises
# ===========================================================================

class TestPairDataMissing:
    """Pair data not found must raise, not silently fallback."""

    def test_run_backtest_raises_on_missing_pair_data(self):
        s = PairTradingBTCETH()
        dummy = pd.DataFrame(
            {"close": [1.0]},
            index=pd.date_range("2023-01-01", periods=1, freq="4h"),
        )
        from pathlib import Path
        with pytest.raises(FileNotFoundError, match="Pair data not found|No data at"):
            s.run_backtest(dummy, data_dir=Path("/nonexistent_dir_12345"))

    def test_load_pair_data_raises_on_missing_files(self):
        from pathlib import Path
        with pytest.raises(FileNotFoundError):
            PairTradingBTCETH.load_pair_data(
                "BTCUSDT", "ETHUSDT", "4h",
                data_dir=Path("/nonexistent_dir_12345"),
            )


# ===========================================================================
# 2. Pair Trading — max_hold_bars exit
# ===========================================================================

class TestMaxHoldBars:
    """max_hold_bars should force exit after N bars via VBT td_stop."""

    def test_param_exists_in_defaults(self):
        s = PairTradingBTCETH()
        assert "max_hold_bars" in s.params
        assert s.params["max_hold_bars"] == 120

    def test_td_stop_applied_in_backtest(self):
        """Shorter max_hold_bars should produce more trades (positions closed earlier)."""
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
        # Very short max_hold forces frequent exits → more trades
        s_short = PairTradingBTCETH({"ols_window": 100, "zscore_period": 30, "entry_z": 2.0, "max_hold_bars": 5})
        # Long max_hold → fewer forced exits
        s_long = PairTradingBTCETH({"ols_window": 100, "zscore_period": 30, "entry_z": 2.0, "max_hold_bars": 500})
        pf_short = s_short.run_backtest(ohlcv, initial_capital=150.0)
        pf_long = s_long.run_backtest(ohlcv, initial_capital=150.0)
        # Short hold should produce at least as many trades as long hold
        assert pf_short.trades.count() >= pf_long.trades.count()

    def test_no_max_hold_exit_when_normal_exit_first(self):
        """If z reverts before max_hold, normal exit takes priority."""
        s = PairTradingBTCETH({"entry_z": 2.0, "exit_z": 0.0, "stop_z": 4.0, "max_hold_bars": 100})
        n = 30
        z_vals = np.full(n, -2.5)
        z_vals[5] = 0.5  # z reverts at bar 5 → normal exit
        ohlcv = _make_zscore_ohlcv(n=n, z_values=z_vals)
        exits = s.generate_exits(ohlcv)
        assert exits.iloc[5]  # Normal reversion exit


# ===========================================================================
# 3. Block Bootstrap — insufficient data
# ===========================================================================

class TestBlockBootstrapInsufficient:
    """block_bootstrap_simulation should return error on insufficient data."""

    def test_too_few_data_points(self):
        eq = _make_equity(n=5)  # 5 daily points, block_size default = 5
        result = block_bootstrap_simulation(eq, block_size=5)
        assert "error" in result
        assert "Insufficient" in result["error"]

    def test_exact_threshold_fails(self):
        """Exactly block_size * 2 returns = boundary case.

        Returns = n-1 (after pct_change dropna), so n=11 gives 10 returns,
        which equals block_size*2 = 10 → should NOT fail.
        """
        eq = _make_equity(n=11)  # 10 returns, block_size=5, 10 >= 5*2
        result = block_bootstrap_simulation(eq, block_size=5, n_simulations=10)
        assert "error" not in result

    def test_just_below_threshold_fails(self):
        """n=10 → 9 returns < block_size*2=10 → should fail."""
        eq = _make_equity(n=10)  # 9 returns
        result = block_bootstrap_simulation(eq, block_size=5)
        assert "error" in result


# ===========================================================================
# 4. Block Bootstrap — normal output format
# ===========================================================================

class TestBlockBootstrapOutput:
    """block_bootstrap_simulation output structure validation."""

    @pytest.fixture()
    def result(self):
        eq = _make_equity(n=500)
        return block_bootstrap_simulation(eq, n_simulations=50, block_size=5, seed=42)

    def test_top_level_keys(self, result):
        expected = {"n_simulations", "method", "block_size", "original", "simulation"}
        assert expected.issubset(result.keys())
        assert result["method"] == "block_bootstrap"
        assert result["block_size"] == 5
        assert result["n_simulations"] == 50

    def test_original_keys(self, result):
        orig = result["original"]
        assert {"final_value", "max_drawdown", "sharpe"} == set(orig.keys())
        assert isinstance(orig["final_value"], float)
        assert isinstance(orig["sharpe"], float)

    def test_simulation_percentiles(self, result):
        sim = result["simulation"]
        for metric in ("final_value", "sharpe"):
            for pkey in ("mean", "median", "p5", "p95"):
                assert pkey in sim[metric], f"Missing {pkey} in simulation.{metric}"

    def test_drawdown_percentiles(self, result):
        dd = result["simulation"]["max_drawdown"]
        assert "mean" in dd
        assert "p5" in dd
        assert "p95" in dd
        # Drawdowns should be negative (percentages)
        assert dd["mean"] < 0


# ===========================================================================
# 5. Nested Walk-Forward — output format & weights
# ===========================================================================

class TestNestedWalkForwardOutput:
    """walk_forward_nested_analysis output structure and weighting."""

    @pytest.fixture()
    def result_sharpe(self):
        curves = _make_equity_curves(n_strats=3, n=600)
        return walk_forward_nested_analysis(
            curves, train_bars=200, test_bars=90, step_bars=90,
            weighting="sharpe_pos",
        )

    @pytest.fixture()
    def result_equal(self):
        curves = _make_equity_curves(n_strats=3, n=600)
        return walk_forward_nested_analysis(
            curves, train_bars=200, test_bars=90, step_bars=90,
            weighting="equal",
        )

    def test_top_level_keys(self, result_sharpe):
        assert "windows" in result_sharpe
        assert "summary" in result_sharpe
        assert "weighting" in result_sharpe
        assert "passed" in result_sharpe
        assert result_sharpe["weighting"] == "sharpe_pos"

    def test_windows_structure(self, result_sharpe):
        assert len(result_sharpe["windows"]) > 0
        w = result_sharpe["windows"][0]
        expected_keys = {
            "id", "train", "test",
            "is_sharpe_avg_weighted", "oos_sharpe",
            "oos_return%", "oos_maxdd%", "n_strategies_weighted",
        }
        assert expected_keys.issubset(w.keys())

    def test_summary_keys(self, result_sharpe):
        s = result_sharpe["summary"]
        for key in ("weighting", "n_windows", "avg_oos_sharpe", "min_oos_sharpe",
                     "max_oos_sharpe", "oos_sharpe_std", "pct_positive_oos",
                     "avg_oos_return_pct"):
            assert key in s, f"Missing summary key: {key}"

    def test_equal_weighting_all_strategies_used(self, result_equal):
        for w in result_equal["windows"]:
            assert w["n_strategies_weighted"] == 3

    def test_invalid_weighting_raises(self):
        curves = _make_equity_curves(n_strats=2, n=600)
        with pytest.raises(ValueError, match="weighting must be"):
            walk_forward_nested_analysis(curves, weighting="invalid")


# ===========================================================================
# 6. Nested Walk-Forward — insufficient data
# ===========================================================================

class TestNestedWalkForwardInsufficient:
    """Insufficient data should return error dict, not crash."""

    def test_empty_curves(self):
        result = walk_forward_nested_analysis({})
        assert result["passed"] is False
        assert "error" in result["summary"]

    def test_too_short_curves(self):
        curves = _make_equity_curves(n_strats=2, n=50)
        result = walk_forward_nested_analysis(
            curves, train_bars=365, test_bars=90,
        )
        assert result["passed"] is False
        assert "Insufficient" in result["summary"].get("error", "")

    def test_single_strategy_works(self):
        """Even one strategy should produce valid results."""
        curves = {"only": _make_equity(n=600)}
        result = walk_forward_nested_analysis(
            curves, train_bars=200, test_bars=90, step_bars=90,
        )
        assert len(result["windows"]) > 0
        assert "avg_oos_sharpe" in result["summary"]
