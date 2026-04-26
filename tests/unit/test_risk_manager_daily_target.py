"""Unit tests for RiskManager daily profit target gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.constants import EventType, OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent


def _make_risk_manager(daily_profit_target_usd: float = 20.0, equity: float = 110.0):
    from bot.risk.risk_manager import RiskManager
    bus = EventBus()
    rm = RiskManager(
        event_bus=bus,
        daily_profit_target_usd=daily_profit_target_usd,
    )
    rm.set_equity(equity)
    return rm, bus


def _make_fill(pnl: float, strategy: str = "test") -> FillEvent:
    return FillEvent(
        timestamp=datetime.now(UTC),
        strategy_name=strategy,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.001,
        price=50000.0,
        realized_pnl=pnl,
        source="test",
    )


class TestDailyProfitTargetGate:
    def test_conservative_mode_false_initially(self):
        rm, _ = _make_risk_manager()
        assert rm.conservative_mode is False

    def test_conservative_mode_flips_when_target_hit(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        fill = _make_fill(pnl=25.0)
        bus.publish(fill)
        assert rm.conservative_mode is True

    def test_conservative_mode_not_flipped_below_target(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        fill = _make_fill(pnl=15.0)
        bus.publish(fill)
        assert rm.conservative_mode is False

    def test_conservative_mode_flips_at_exact_target(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        bus.publish(_make_fill(pnl=10.0))
        assert rm.conservative_mode is False
        bus.publish(_make_fill(pnl=10.0))
        assert rm.conservative_mode is True

    def test_daily_target_hit_event_published(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        received = []
        bus.subscribe(EventType.DAILY_TARGET_HIT.value, received.append)
        bus.publish(_make_fill(pnl=25.0))
        assert len(received) == 1
        assert received[0].daily_pnl >= 20.0
        assert received[0].target_usd == 20.0

    def test_event_published_only_once_per_day(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        received = []
        bus.subscribe(EventType.DAILY_TARGET_HIT.value, received.append)
        bus.publish(_make_fill(pnl=25.0))
        bus.publish(_make_fill(pnl=5.0))  # still above target
        assert len(received) == 1

    def test_disabled_when_target_is_zero(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=0.0)
        bus.publish(_make_fill(pnl=100.0))
        assert rm.conservative_mode is False

    def test_conservative_mode_resets_at_utc_midnight(self):
        from bot.core.clock import BaseClock

        class FakeClock(BaseClock):
            def __init__(self, dt):
                self._dt = dt
            def now(self):
                return self._dt
            def now_ms(self) -> int:
                return int(self._dt.timestamp() * 1000)
            def sleep(self, seconds: float) -> None:
                pass

        from bot.risk.risk_manager import RiskManager
        bus = EventBus()
        today = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)
        clock = FakeClock(today)
        rm = RiskManager(
            event_bus=bus,
            daily_profit_target_usd=20.0,
            clock=clock,
        )
        rm.set_equity(110.0)

        # Hit target today
        bus.publish(_make_fill(pnl=25.0))
        assert rm.conservative_mode is True

        # Advance clock to next day
        clock._dt = datetime(2025, 1, 16, 0, 1, 0, tzinfo=UTC)
        # Trigger daily reset via check_signal path
        from bot.core.events import SignalEvent
        sig = SignalEvent(
            timestamp=clock.now(),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.001,
            price=50000.0,
            source="test",
        )
        rm.check_signal(sig, equity=110.0)

        assert rm.conservative_mode is False
        assert rm._daily_pnl == 0.0

    def test_daily_pnl_property_returns_cumulative(self):
        rm, bus = _make_risk_manager()
        bus.publish(_make_fill(pnl=5.0))
        bus.publish(_make_fill(pnl=8.0))
        assert rm.daily_pnl == pytest.approx(13.0)

    def test_get_status_includes_conservative_mode(self):
        rm, bus = _make_risk_manager(daily_profit_target_usd=20.0)
        bus.publish(_make_fill(pnl=25.0))
        status = rm.get_status()
        assert status["conservative_mode"] is True
        assert status["daily_profit_target_usd"] == 20.0
