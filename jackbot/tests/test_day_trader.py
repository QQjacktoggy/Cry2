"""Tests for the DayTrader orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jackbot.core.constants import TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, MarketEvent
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig


def _make_bar(symbol: str, price: float, offset: int = 0) -> MarketEvent:
    return MarketEvent(
        timestamp=datetime(2026, 4, 24, 12, offset % 60, 0, tzinfo=UTC),
        symbol=symbol,
        timeframe="5m",
        open=price - 10,
        high=price + 20,
        low=price - 20,
        close=price,
        volume=1000.0,
    )


def _warmup(trader: DayTrader, symbol: str, price: float, bars: int = 60):
    """Feed enough bars with a mild uptrend to build sufficient ADX/confidence."""
    for i in range(bars):
        # Mild uptrend + oscillation -> produces ADX > 20 and reasonable BB width
        trend = i * 30  # stronger trend
        oscillation = (i % 6 - 3) * 50
        p = price + trend + oscillation
        bar = MarketEvent(
            timestamp=datetime(2026, 4, 24, 10, i // 60, i % 60, tzinfo=UTC),
            symbol=symbol,
            timeframe="5m",
            open=p - 30,
            high=p + 60,
            low=p - 60,
            close=p,
            volume=500.0,
        )
        trader.on_bar(bar)


class TestDayTraderLifecycle:
    def test_warmup_no_signals(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)

        bar = _make_bar("BTCUSDT", 95000.0)
        signals = trader.on_bar(bar)
        assert len(signals) == 0

    def test_creates_grid_after_warmup(self):
        bus = EventBus()
        # Lower confidence requirement for testing
        config = DayTraderConfig(warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)
        trader.mark_warmup_complete()

        # Next bar should trigger grid creation
        bar = _make_bar("BTCUSDT", 97000.0, offset=55)
        signals = trader.on_bar(bar)

        # Should have created a grid and returned initial order signals
        assert len(signals) > 0

    def test_mode_starts_aggressive(self):
        bus = EventBus()
        config = DayTraderConfig()
        trader = DayTrader(config=config, event_bus=bus)
        assert trader.mode == TradingMode.AGGRESSIVE

    def test_conservative_switch_on_target(self):
        bus = EventBus()
        config = DayTraderConfig(daily_profit_target_usd=2.0)
        trader = DayTrader(config=config, event_bus=bus)
        assert trader.mode == TradingMode.AGGRESSIVE

        # Directly simulate profit accumulation via the internal mechanism
        trader._daily_profit = 1.5
        assert trader.mode == TradingMode.AGGRESSIVE

        # Simulate crossing the target
        trader._daily_profit = 2.5
        trader._switch_to_conservative()
        assert trader.mode == TradingMode.CONSERVATIVE

    def test_halts_on_loss_limit(self):
        bus = EventBus()
        # Set a very low limit to trigger easily
        config = DayTraderConfig(daily_loss_limit_pct=1.0, total_capital_usd=100.0, warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)
        trader.mark_warmup_complete()

        _warmup(trader, "BTCUSDT", 95000.0)
        bar = _make_bar("BTCUSDT", 95000.0, offset=55)
        trader.on_bar(bar)

        # Simulate losses > 1.0 USDT
        fill = FillEvent(
            timestamp=datetime.now(UTC),
            symbol="BTCUSDT",
            side="SELL",
            quantity=0.001,
            price=94000.0,
            realized_pnl=-2.0,
            grid_id="fake_grid",
            level_index=0,
        )
        trader.on_fill(fill)

        assert trader.is_halted

    def test_daily_reset_clears_state(self):
        bus = EventBus()
        trader = DayTrader(config=DayTraderConfig(), event_bus=bus)
        trader._daily_profit = 50.0
        trader._daily_loss = 10.0
        trader._halted = True
        
        # Manually trigger reset
        trader._last_reset_date = "2020-01-01"
        trader._check_daily_reset()
        
        assert trader.daily_profit == 0.0
        assert not trader.is_halted


class TestDayTraderStatus:
    def test_get_status(self):
        bus = EventBus()
        trader = DayTrader(config=DayTraderConfig(), event_bus=bus)
        status = trader.get_status()
        assert "mode" in status
        assert "daily_profit" in status
        assert "active_grids" in status


class TestHourlyReview:
    def test_review_not_triggered_before_interval(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50, hourly_review_interval_bars=12)
        trader = DayTrader(config=config, event_bus=bus)
        trader.mark_warmup_complete()

        _warmup(trader, "BTCUSDT", 95000.0)
        
        # Feed 5 bars since last review (which was at bar 0)
        for i in range(5):
            bar = _make_bar("BTCUSDT", 95000.0, offset=i)
            trader.on_bar(bar)
            
        assert trader._last_review_bar.get("BTCUSDT", 0) == 0

    def test_review_triggered_at_interval(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50, hourly_review_interval_bars=12)
        trader = DayTrader(config=config, event_bus=bus)
        trader.mark_warmup_complete()

        _warmup(trader, "BTCUSDT", 95000.0)

        # Feed 13 bars after warmup
        for i in range(13):
            bar = _make_bar("BTCUSDT", 95000.0 + i * 20, offset=i)
            trader.on_bar(bar)

        # Review should have happened at some point
        assert trader._last_review_bar.get("BTCUSDT", 0) > 0


class TestStopLoss:
    def test_stop_loss_closes_grid(self):
        bus = EventBus()
        config = DayTraderConfig(grid_stop_loss_pct=2.0, warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)
        trader.mark_warmup_complete()
        
        _warmup(trader, "BTCUSDT", 95000.0)
        
        # Force create a grid
        grid, _ = trader._engine.create_grid(
            "BTCUSDT", trader._assessor.assess("BTCUSDT").direction,
            100000, 90000, 10, 10, 100, 95000
        )
        
        # Update PnL to hit stop loss (-3 USDT < -2% of 100)
        grid.unrealized_pnl = -3.0
        
        # Next bar should trigger stop loss
        bar = _make_bar("BTCUSDT", 95000.0)
        signals = trader.on_bar(bar)
        
        assert any(s.cancel_order_id != "" for s in signals)
        assert grid.closed
        assert grid.close_reason == "stop_loss"
