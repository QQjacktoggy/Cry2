"""Tests for risk management modules."""

from datetime import datetime, timezone

from bot.core.constants import OrderSide, EventType
from bot.core.events import FillEvent, SignalEvent
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch, KillSwitchState
from bot.risk.position_sizer import PositionSizer
from bot.core.events import MarketEvent


class TestPositionSizer:
    def test_fixed_fractional(self):
        sizer = PositionSizer(max_risk_per_trade_pct=1.0)
        qty = sizer.calculate_fixed_fractional(
            equity=10000.0,
            entry_price=42000.0,
            stop_loss_price=41000.0,
            leverage=1,
        )
        # Risk = $100, Stop distance = $1000, Qty = 0.1
        assert abs(qty - 0.1) < 0.001

    def test_zero_stop_distance(self):
        sizer = PositionSizer()
        qty = sizer.calculate_fixed_fractional(
            equity=10000.0,
            entry_price=42000.0,
            stop_loss_price=42000.0,
        )
        assert qty == 0.0

    def test_atr_based(self):
        sizer = PositionSizer(max_risk_per_trade_pct=1.0)
        qty = sizer.calculate_atr_based(
            equity=10000.0,
            entry_price=42000.0,
            atr=500.0,
            atr_multiplier=2.0,
            leverage=1,
        )
        # Risk = $100, Stop = 2*500 = $1000, Qty = 0.1
        assert abs(qty - 0.1) < 0.001


class TestCircuitBreaker:
    def test_normal_bar(self):
        cb = CircuitBreaker(bar_change_threshold_pct=5.0)
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=42100.0,
            low=41900.0,
            close=42050.0,
            volume=1000.0,
        )
        assert cb.check_bar(event) is True
        assert cb.is_tripped is False

    def test_extreme_bar(self):
        cb = CircuitBreaker(bar_change_threshold_pct=5.0)
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=45000.0,
            low=42000.0,
            close=45000.0,  # ~7.1% move
            volume=1000.0,
        )
        assert cb.check_bar(event) is False
        assert cb.is_tripped is True

    def test_reset(self):
        cb = CircuitBreaker(bar_change_threshold_pct=5.0)
        event = MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            timeframe="4h",
            open=42000.0,
            high=45000.0,
            low=42000.0,
            close=45000.0,
            volume=1000.0,
        )
        cb.check_bar(event)
        cb.reset()
        assert cb.is_tripped is False


class TestKillSwitch:
    def test_initial_state(self, event_bus):
        ks = KillSwitch(event_bus=event_bus)
        assert ks.state == KillSwitchState.ARMED
        assert ks.is_triggered is False

    def test_trigger(self, event_bus):
        ks = KillSwitch(event_bus=event_bus)
        ks.trigger("test reason", "user")
        assert ks.is_triggered is True
        assert ks.state == KillSwitchState.TRIGGERED

    def test_consecutive_errors(self, event_bus):
        ks = KillSwitch(event_bus=event_bus, max_consecutive_errors=3)
        ks.record_error()
        ks.record_error()
        assert ks.is_triggered is False
        ks.record_error()
        assert ks.is_triggered is True

    def test_success_resets_errors(self, event_bus):
        ks = KillSwitch(event_bus=event_bus, max_consecutive_errors=3)
        ks.record_error()
        ks.record_error()
        ks.record_success()
        ks.record_error()
        assert ks.is_triggered is False

    def test_resolve_and_rearm(self, event_bus):
        ks = KillSwitch(event_bus=event_bus)
        ks.trigger("test")
        ks.resolve()
        assert ks.state == KillSwitchState.RESOLVED
        ks.rearm()
        assert ks.state == KillSwitchState.ARMED
