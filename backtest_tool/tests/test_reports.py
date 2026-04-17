"""Tests for HTML report generation."""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest_tool.reports.charts import (
    equity_curve_chart,
    drawdown_chart,
    monthly_heatmap,
    daily_pnl_bar,
    trade_pnl_histogram,
    equity_overlay,
)
from backtest_tool.reports.trade_log import render_trade_log
from backtest_tool.reports.tearsheet import render_monthly_table, render_yearly_table


def _make_equity(n: int = 365) -> pd.Series:
    """Create synthetic equity curve."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, n)
    prices = 10000 * np.cumprod(1 + returns)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(prices, index=idx)


def _make_trades(n: int = 20) -> pd.DataFrame:
    """Create synthetic trades DataFrame."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "Entry Timestamp": pd.date_range("2023-01-01", periods=n, freq="7D"),
        "Exit Timestamp": pd.date_range("2023-01-03", periods=n, freq="7D"),
        "Entry Price": 30000 + rng.normal(0, 500, n),
        "Exit Price": 30000 + rng.normal(100, 500, n),
        "Size": rng.uniform(0.01, 0.1, n),
        "PnL": rng.normal(50, 200, n),
        "Fees": rng.uniform(1, 10, n),
        "Direction": ["Long"] * (n // 2) + ["Short"] * (n - n // 2),
    })


class TestCharts:
    def setup_method(self):
        self.equity = _make_equity()

    def test_equity_curve_returns_html(self):
        html = equity_curve_chart(self.equity)
        assert "<div" in html
        assert "plotly" in html.lower() or "js-plotly" in html.lower()

    def test_drawdown_chart_returns_html(self):
        html = drawdown_chart(self.equity)
        assert "<div" in html

    def test_monthly_heatmap_returns_html(self):
        html = monthly_heatmap(self.equity)
        assert "<div" in html

    def test_daily_pnl_returns_html(self):
        html = daily_pnl_bar(self.equity)
        assert "<div" in html

    def test_trade_histogram_returns_html(self):
        trades = _make_trades()
        html = trade_pnl_histogram(trades)
        assert "<div" in html

    def test_equity_overlay_multiple(self):
        equities = {
            "Strategy A": _make_equity(),
            "Strategy B": _make_equity(),
        }
        html = equity_overlay(equities)
        assert "<div" in html
        assert "Strategy A" in html


class TestTradeLog:
    def test_render_trade_log(self):
        trades = _make_trades()
        html = render_trade_log(trades, symbol="BTCUSDT")
        assert "trades-table" in html
        assert "BTCUSDT" in html or "合計" in html

    def test_empty_trades(self):
        html = render_trade_log(pd.DataFrame(), symbol="BTCUSDT")
        assert "無交易" in html


class TestTearsheet:
    def setup_method(self):
        self.equity = _make_equity()

    def test_monthly_table_has_months(self):
        html = render_monthly_table(self.equity)
        assert "Jan" in html
        assert "年份" in html

    def test_yearly_table_has_years(self):
        html = render_yearly_table(self.equity)
        assert "2023" in html

    def test_empty_equity(self):
        empty = pd.Series(dtype=float)
        html = render_monthly_table(empty)
        assert "資料不足" in html
