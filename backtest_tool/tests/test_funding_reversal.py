"""Tests for FundingReversalVBT strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest_tool.strategies.funding_reversal_vbt import FundingReversalVBT


def _make_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="8h")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 10)  # prevent negative prices
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + abs(rng.normal(0, 0.01, n))),
            "low": close * (1 - abs(rng.normal(0, 0.01, n))),
            "close": close,
            "volume": rng.uniform(100, 10000, n),
        },
        index=dates,
    )


def _make_funding(n: int = 100) -> pd.DataFrame:
    """Generate synthetic funding rate data."""
    dates = pd.date_range("2024-01-01", periods=n, freq="8h")
    # Mostly small, with a few extreme values
    rates = np.zeros(n)
    rates[10:15] = -0.001  # Negative funding period (~-10.95% annualized)
    rates[50:55] = 0.003   # High positive funding (~32.85% annualized)
    rates[80:83] = -0.002  # Extreme negative (~-21.9% annualized)
    return pd.DataFrame({"funding_rate": rates}, index=dates)


class TestFundingReversalVBT:
    """Tests for funding reversal strategy."""

    def test_default_params(self):
        strat = FundingReversalVBT()
        assert strat.params["entry_rate_long"] == -8.0
        assert strat.params["entry_rate_short"] == 999.0  # shorts disabled
        assert strat.params["max_hold_days"] == 2
        assert strat.required_timeframe == "8h"

    def test_custom_params(self):
        strat = FundingReversalVBT({"entry_rate_long": -12.0, "max_hold_days": 3})
        assert strat.params["entry_rate_long"] == -12.0
        assert strat.params["max_hold_days"] == 3
        # Defaults preserved for unset params
        assert strat.params["roc_period"] == 9

    def test_prepare_data_merges_funding(self):
        klines = _make_ohlcv()
        funding = _make_funding()
        merged = FundingReversalVBT.prepare_data(klines, funding)
        assert "funding_rate" in merged.columns
        assert "annual_rate" in merged.columns
        # Annualized rate should be funding_rate * 3 * 365 * 100
        expected = funding["funding_rate"].iloc[10] * 3 * 365 * 100
        assert abs(merged["annual_rate"].iloc[10] - expected) < 0.01

    def test_prepare_data_empty_funding(self):
        klines = _make_ohlcv()
        funding = pd.DataFrame(columns=["funding_rate"])
        merged = FundingReversalVBT.prepare_data(klines, funding)
        assert (merged["annual_rate"] == 0.0).all()

    def test_no_signals_without_annual_rate(self):
        ohlcv = _make_ohlcv()
        strat = FundingReversalVBT()
        entries = strat.generate_entries(ohlcv)
        assert not entries.any()

    def test_long_entries_on_extreme_negative(self):
        klines = _make_ohlcv(200)
        funding = _make_funding(200)
        merged = FundingReversalVBT.prepare_data(klines, funding)
        # Use threshold that captures the -21.9% funding period
        strat = FundingReversalVBT({"entry_rate_long": -20, "confirm_bars": 1, "roc_confirm_long": 999})
        entries = strat.generate_entries(merged)
        assert entries.sum() > 0, "Should have long entries on extreme negative funding"

    def test_shorts_disabled_by_default(self):
        klines = _make_ohlcv(200)
        funding = _make_funding(200)
        merged = FundingReversalVBT.prepare_data(klines, funding)
        strat = FundingReversalVBT()
        short_entries = strat.generate_short_entries(merged)
        assert not short_entries.any(), "Shorts should be disabled with threshold=999"

    def test_run_backtest_long_only(self):
        klines = _make_ohlcv(300)
        funding = _make_funding(300)
        merged = FundingReversalVBT.prepare_data(klines, funding)
        strat = FundingReversalVBT({"entry_rate_long": -5, "confirm_bars": 1, "roc_confirm_long": 999})
        pf = strat.run_backtest(merged, initial_capital=1000.0)
        assert pf is not None
        assert len(pf.value()) == 300

    def test_run_backtest_auto_load_funding(self):
        """Test that run_backtest works even without pre-merged funding data."""
        klines = _make_ohlcv(100)
        strat = FundingReversalVBT()
        # No symbol provided → uses zero funding rates → no signals → flat equity
        pf = strat.run_backtest(klines, initial_capital=1000.0)
        assert pf is not None
        # With no signals, portfolio should stay near initial capital
        assert abs(pf.value().iloc[-1] - 1000.0) < 1.0

    def test_consecutive_count(self):
        strat = FundingReversalVBT()
        cond = pd.Series([False, True, True, True, False, True, True])
        result = strat._consecutive_count(cond)
        # When condition is False → 0
        assert result.iloc[0] == 0
        assert result.iloc[4] == 0
        # When condition is True → incrementing count
        assert result.iloc[1] > 0
        assert result.iloc[2] > result.iloc[1]
        assert result.iloc[3] > result.iloc[2]
        # Second run resets
        assert result.iloc[5] > 0
        assert result.iloc[6] > result.iloc[5]

    def test_bars_since_true(self):
        signal = pd.Series([False, True, False, False, True, False])
        result = FundingReversalVBT._bars_since_true(signal)
        # After first True (idx 1): 0, 1, 2
        # After second True (idx 4): 0, 1
        assert result.iloc[2] == 1  # 1 bar after first True
        assert result.iloc[5] == 1  # 1 bar after second True

    def test_strategy_in_map(self):
        from backtest_tool.strategies import STRATEGY_MAP
        assert "funding_reversal" in STRATEGY_MAP
        assert STRATEGY_MAP["funding_reversal"] is FundingReversalVBT
