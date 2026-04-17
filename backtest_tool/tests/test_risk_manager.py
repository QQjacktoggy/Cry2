"""Tests for backtest_tool engine risk management components."""

import numpy as np
import pandas as pd
import pytest

from backtest_tool.engine.risk_manager import (
    ConsecutiveLossGuard,
    PortfolioStopLoss,
    VolatilityLeverageAdapter,
)


def _make_equity(n: int = 200, drop_at: int | None = None) -> pd.Series:
    """Create a synthetic equity curve."""
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    values = [10000.0] * n
    if drop_at is not None:
        for i in range(drop_at, n):
            values[i] = 10000.0 * 0.70  # 30% drop
    return pd.Series(values, index=idx)


def _make_ohlcv(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    high = close + rng.uniform(50, 300, n)
    low = close - rng.uniform(50, 300, n)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": rng.uniform(100, 1000, n)},
        index=idx,
    )


class TestPortfolioStopLoss:
    def test_portfolio_stop_loss_mask_shape(self):
        equity = _make_equity(200)
        psl = PortfolioStopLoss(max_drawdown_pct=20.0, cooldown_bars=10)
        mask = psl.generate_mask(equity)
        assert len(mask) == len(equity)

    def test_portfolio_stop_loss_triggers_at_threshold(self):
        """Equity drops 30% → some bars should be False with threshold=20%."""
        idx = pd.date_range("2023-01-01", periods=100, freq="4h")
        values = [10000.0] * 50 + [7000.0] * 50  # 30% drop
        equity = pd.Series(values, index=idx)
        psl = PortfolioStopLoss(max_drawdown_pct=20.0, cooldown_bars=5)
        mask = psl.generate_mask(equity)
        assert (~mask).any(), "Expected some paused bars when drawdown exceeds threshold"

    def test_portfolio_stop_loss_no_trigger_when_below(self):
        """Equity never drops more than 5% → no paused bars with threshold=20%."""
        idx = pd.date_range("2023-01-01", periods=100, freq="4h")
        values = [10000.0 + i * 10 for i in range(100)]  # monotonically increasing
        equity = pd.Series(values, index=idx)
        psl = PortfolioStopLoss(max_drawdown_pct=20.0, cooldown_bars=5)
        mask = psl.generate_mask(equity)
        assert mask.all(), "No bars should be paused when drawdown never exceeds threshold"

    def test_portfolio_stop_loss_events_recorded(self):
        """After trigger, events list is non-empty."""
        idx = pd.date_range("2023-01-01", periods=100, freq="4h")
        values = [10000.0] * 40 + [7000.0] * 60  # 30% drop triggers at threshold=20%
        equity = pd.Series(values, index=idx)
        psl = PortfolioStopLoss(max_drawdown_pct=20.0, cooldown_bars=5)
        psl.generate_mask(equity)
        assert len(psl.events) > 0


class TestConsecutiveLossGuard:
    def test_consecutive_loss_guard_empty_pnl(self):
        """Empty trade PnL → all bars True."""
        idx = pd.date_range("2023-01-01", periods=100, freq="4h")
        empty_pnl = pd.Series(dtype=float)
        guard = ConsecutiveLossGuard(max_consecutive_losses=5, pause_bars=10)
        mask = guard.generate_mask(empty_pnl, idx)
        assert mask.all()
        assert len(mask) == len(idx)

    def test_consecutive_loss_guard_triggers(self):
        """5 consecutive -1 PnL trades triggers pause."""
        bar_idx = pd.date_range("2023-01-01", periods=200, freq="4h")
        # Place 6 consecutive losses within the bar range
        trade_times = pd.date_range("2023-01-10", periods=6, freq="8h")
        pnl = pd.Series([-1.0] * 6, index=trade_times)
        guard = ConsecutiveLossGuard(max_consecutive_losses=5, pause_bars=20)
        mask = guard.generate_mask(pnl, bar_idx)
        assert (~mask).any(), "Expected some paused bars after consecutive losses"


class TestVolatilityLeverageAdapter:
    def test_volatility_leverage_adapter_output_shape(self):
        ohlcv = _make_ohlcv(300)
        adapter = VolatilityLeverageAdapter(max_leverage=3, min_leverage=1)
        leverage = adapter.compute(ohlcv)
        assert len(leverage) == len(ohlcv)

    def test_volatility_leverage_adapter_values_bounded(self):
        ohlcv = _make_ohlcv(300)
        adapter = VolatilityLeverageAdapter(max_leverage=3, min_leverage=1)
        leverage = adapter.compute(ohlcv)
        assert leverage.min() >= 1.0 - 1e-6
        assert leverage.max() <= 3.0 + 1e-6
