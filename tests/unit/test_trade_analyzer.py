"""Unit tests for TradeAnalyzer."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.core.constants import OrderSide
from bot.core.events import FillEvent
from bot.portfolio.trade_analyzer import TradeAnalyzer
from bot.portfolio.trade_journal import TradeJournal


@pytest.fixture
def journal(tmp_path):
    j = TradeJournal(tmp_path / "test.db")
    yield j
    j.close()


def _fill(strategy, side, price, realized_pnl=0.0, ts=None):
    return FillEvent(
        timestamp=ts or datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        strategy_name=strategy,
        symbol="BTCUSDT",
        side=side,
        quantity=0.01,
        price=price,
        commission=0.1,
        realized_pnl=realized_pnl,
        source="test",
    )


class TestBuildEquityCurves:
    def test_empty_journal_returns_empty(self, journal):
        analyzer = TradeAnalyzer(journal, initial_capital=10_000)
        assert analyzer.build_equity_curves() == {}

    def test_single_strategy_curve(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=100.0))
        journal.record_fill(_fill("trend", OrderSide.SELL, 51000.0, realized_pnl=200.0))

        analyzer = TradeAnalyzer(journal, initial_capital=10_000)
        curves = analyzer.build_equity_curves()

        assert "trend" in curves
        assert len(curves["trend"]) == 2
        # Cumulative realized_pnl = 300, so final equity = 10300
        assert curves["trend"].iloc[-1] == pytest.approx(10_300.0)

    def test_multi_strategy_separate_curves(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=50.0))
        journal.record_fill(_fill("grid", OrderSide.BUY, 50000.0, realized_pnl=30.0))

        analyzer = TradeAnalyzer(journal, initial_capital=10_000)
        curves = analyzer.build_equity_curves()

        assert set(curves.keys()) == {"trend", "grid"}

    def test_strategy_filter(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=10.0))
        journal.record_fill(_fill("grid", OrderSide.BUY, 50000.0, realized_pnl=5.0))

        analyzer = TradeAnalyzer(journal, initial_capital=10_000)
        curves = analyzer.build_equity_curves(strategy="trend")

        assert "trend" in curves
        assert "grid" not in curves


class TestTradeSummary:
    def test_empty_returns_overall_zero(self, journal):
        analyzer = TradeAnalyzer(journal)
        result = analyzer.trade_summary()
        assert result["overall"]["total_fills"] == 0
        assert result["per_strategy"] == {}

    def test_per_strategy_breakdown(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0))
        journal.record_fill(_fill("grid", OrderSide.BUY, 50000.0))

        analyzer = TradeAnalyzer(journal)
        result = analyzer.trade_summary()

        assert result["overall"]["total_fills"] == 2
        assert "trend" in result["per_strategy"]
        assert "grid" in result["per_strategy"]


class TestHealthReport:
    def test_empty_journal_returns_empty(self, journal):
        analyzer = TradeAnalyzer(journal)
        assert analyzer.health_report() == {}

    def test_calls_analytics_function(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=10.0))
        analyzer = TradeAnalyzer(journal, initial_capital=10_000)

        mock_result = {"trend": {"sharpe_30": 1.5, "decay": False}}
        with patch(
            "bot.portfolio.trade_analyzer.compute_strategy_health_report",
            return_value=mock_result,
        ) as mock_fn:
            result = analyzer.health_report()

        mock_fn.assert_called_once()
        assert result == mock_result


class TestCorrelationMatrix:
    def test_empty_journal_returns_empty_df(self, journal):
        analyzer = TradeAnalyzer(journal)
        df = analyzer.correlation_matrix()
        assert df.empty

    def test_calls_analytics_function(self, journal):
        for strat in ("trend", "grid"):
            journal.record_fill(_fill(strat, OrderSide.BUY, 50000.0, realized_pnl=10.0))

        analyzer = TradeAnalyzer(journal, initial_capital=10_000)
        mock_df = pd.DataFrame({"trend": [1.0, 0.3], "grid": [0.3, 1.0]},
                               index=["trend", "grid"])
        with patch(
            "bot.portfolio.trade_analyzer.compute_correlation_matrix",
            return_value=mock_df,
        ) as mock_fn:
            result = analyzer.correlation_matrix()

        mock_fn.assert_called_once()
        assert result.shape == (2, 2)


class TestRollingSharpe:
    def test_missing_strategy_returns_empty_series(self, journal):
        analyzer = TradeAnalyzer(journal)
        s = analyzer.rolling_sharpe("nonexistent")
        assert isinstance(s, pd.Series)
        assert s.empty

    def test_calls_analytics_function(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=10.0))
        analyzer = TradeAnalyzer(journal, initial_capital=10_000)

        mock_series = pd.Series([1.2, 1.5])
        with patch(
            "bot.portfolio.trade_analyzer.compute_rolling_sharpe",
            return_value=mock_series,
        ) as mock_fn:
            result = analyzer.rolling_sharpe("trend")

        mock_fn.assert_called_once()
        assert len(result) == 2


class TestMonteCarlo:
    def test_missing_strategy_returns_error_dict(self, journal):
        analyzer = TradeAnalyzer(journal)
        result = analyzer.monte_carlo("nonexistent")
        assert "error" in result

    def test_calls_robustness_function(self, journal):
        journal.record_fill(_fill("trend", OrderSide.BUY, 50000.0, realized_pnl=10.0))
        analyzer = TradeAnalyzer(journal, initial_capital=10_000)

        mock_result = {"p5": 9000.0, "p50": 10500.0, "p95": 12000.0}
        with patch(
            "bot.portfolio.trade_analyzer.monte_carlo_simulation",
            return_value=mock_result,
        ) as mock_fn:
            result = analyzer.monte_carlo("trend")

        mock_fn.assert_called_once()
        assert result == mock_result
