"""Tests for explicit leverage modeling helpers."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest_tool.engine.leverage import (
    annualization_factor_for_timeframe,
    apply_leverage_to_equity,
    summarize_equity_curve,
)


def test_apply_leverage_to_equity_compounds_scaled_returns() -> None:
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    equity = pd.Series([100.0, 110.0, 120.0], index=idx)

    leveraged = apply_leverage_to_equity(equity, 2.0)

    assert leveraged.iloc[0] == pytest.approx(100.0)
    assert leveraged.iloc[-1] == pytest.approx(141.8181818)


def test_summarize_equity_curve_returns_expected_keys() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    equity = pd.Series([100.0, 110.0, 121.0, 115.0], index=idx)

    summary = summarize_equity_curve(
        equity,
        annualization_factor=annualization_factor_for_timeframe("1d"),
    )

    assert summary["final_value"] == pytest.approx(115.0)
    assert summary["total_return"] == pytest.approx(15.0)
    assert summary["max_drawdown"] < 0
    assert "sharpe_ratio" in summary
    assert "sortino_ratio" in summary
    assert "calmar_ratio" in summary
