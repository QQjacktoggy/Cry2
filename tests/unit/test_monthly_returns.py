"""Tests for MetricsCalculator.monthly_returns()."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.backtest.metrics import MetricsCalculator


def _ts(year: int, month: int, day: int) -> int:
    """Return millisecond timestamp for a UTC date."""
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


class TestMonthlyReturns:
    """Tests for the monthly_returns method."""

    def test_empty_equity_curve(self) -> None:
        calc = MetricsCalculator(equity_curve=[], fills=[], initial_capital=150.0)
        assert calc.monthly_returns() == []

    def test_single_point(self) -> None:
        curve = [(_ts(2024, 1, 15), 150.0)]
        calc = MetricsCalculator(equity_curve=curve, fills=[], initial_capital=150.0)
        assert calc.monthly_returns() == []

    def test_two_months_growth(self) -> None:
        curve = [
            (_ts(2024, 1, 5), 150.0),
            (_ts(2024, 1, 15), 155.0),
            (_ts(2024, 1, 25), 160.0),
            (_ts(2024, 2, 5), 165.0),
            (_ts(2024, 2, 15), 170.0),
            (_ts(2024, 2, 25), 180.0),
        ]
        calc = MetricsCalculator(equity_curve=curve, fills=[], initial_capital=150.0)
        monthly = calc.monthly_returns()
        assert len(monthly) == 2

        # January
        jan = monthly[0]
        assert jan["year"] == 2024
        assert jan["month"] == 1
        assert jan["start_equity"] == 150.0
        assert jan["end_equity"] == 160.0
        assert jan["profit"] == 10.0
        assert jan["return_pct"] == pytest.approx(10.0 / 150.0, abs=1e-3)

        # February (start = January end)
        feb = monthly[1]
        assert feb["year"] == 2024
        assert feb["month"] == 2
        assert feb["start_equity"] == 160.0
        assert feb["end_equity"] == 180.0
        assert feb["profit"] == 20.0

    def test_negative_month(self) -> None:
        curve = [
            (_ts(2024, 3, 1), 200.0),
            (_ts(2024, 3, 15), 190.0),
            (_ts(2024, 3, 31), 180.0),
        ]
        calc = MetricsCalculator(equity_curve=curve, fills=[], initial_capital=200.0)
        monthly = calc.monthly_returns()
        assert len(monthly) == 1
        assert monthly[0]["profit"] < 0

    def test_included_in_calculate_all(self) -> None:
        curve = [
            (_ts(2024, 1, 1), 150.0),
            (_ts(2024, 1, 31), 160.0),
        ]
        calc = MetricsCalculator(equity_curve=curve, fills=[], initial_capital=150.0)
        all_metrics = calc.calculate_all()
        assert "monthly_returns" in all_metrics
        assert isinstance(all_metrics["monthly_returns"], list)
