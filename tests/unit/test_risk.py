"""Tests for risk management modules."""

from datetime import UTC, datetime, timedelta

from bot.core.constants import OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, MarketEvent, SignalEvent
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch, KillSwitchState
from bot.risk.position_sizer import PositionSizer
from bot.risk.risk_manager import RiskManager


def _make_signal(strategy="trend", symbol="BTCUSDT", qty=0.01, price=50000.0):
    return SignalEvent(
        timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        strategy_name=strategy,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=qty,
        price=price,
        source="test",
    )


def _make_fill(strategy="trend", pnl=0.0, symbol="BTCUSDT"):
    return FillEvent(
        timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        strategy_name=strategy,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=0.01,
        price=50000.0,
        realized_pnl=pnl,
        source="test",
    )


class TestPositionSizer:
    def test_fixed_fractional(self):
        sizer = PositionSizer(max_risk_per_trade_pct=1.0, max_position_value_pct=100.0)
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
        sizer = PositionSizer(max_risk_per_trade_pct=1.0, max_position_value_pct=100.0)
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
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
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
        # Use a timestamp in the far future so the 30-min cooldown is not expired
        # when is_tripped checks against datetime.now(UTC).
        future_ts = datetime.now(UTC) + timedelta(hours=1)
        event = MarketEvent(
            timestamp=future_ts,
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
        future_ts = datetime.now(UTC) + timedelta(hours=1)
        event = MarketEvent(
            timestamp=future_ts,
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


class TestRiskManagerD1:
    """D1 portfolio-level risk controls: drawdown halt and consecutive-loss cooldown."""

    def _rm(self, **kwargs):
        return RiskManager(event_bus=EventBus(), **kwargs)

    # ── Drawdown halt ────────────────────────────────────────────────────

    def _rm_no_daily_weekly(self, **kwargs):
        """RiskManager with daily/weekly limits disabled (set to 100%)."""
        return self._rm(
            daily_loss_limit_pct=100.0,
            weekly_loss_limit_pct=100.0,
            **kwargs,
        )

    def test_drawdown_halt_blocks_signals(self):
        rm = self._rm_no_daily_weekly(max_drawdown_pct=20.0, drawdown_cooldown_bars=8)
        rm.set_equity(10_000)
        rm._peak_equity = 10_000

        # Simulate a 25% drawdown via fill event
        rm.set_equity(7_500)
        rm._on_fill(_make_fill(pnl=-2_500.0))

        sig = _make_signal()
        passed, reason = rm.check_signal(sig, equity=7_500)
        assert passed is False
        assert "drawdown" in reason.lower()

    def test_drawdown_cooldown_expires_after_n_bars(self):
        rm = self._rm_no_daily_weekly(max_drawdown_pct=20.0, drawdown_cooldown_bars=3)
        rm.set_equity(10_000)
        rm._peak_equity = 10_000
        rm.set_equity(7_500)
        rm._on_fill(_make_fill(pnl=-2_500.0))

        assert rm._drawdown_halted is True

        # Tick 3 bars → cooldown expires
        for _ in range(3):
            rm.tick_bar()

        assert rm._drawdown_halted is False
        passed, _ = rm.check_signal(_make_signal(), equity=7_500)
        assert passed is True

    def test_no_halt_below_drawdown_threshold(self):
        rm = self._rm_no_daily_weekly(max_drawdown_pct=20.0)
        rm.set_equity(10_000)
        rm._peak_equity = 10_000
        rm.set_equity(9_500)
        rm._on_fill(_make_fill(pnl=-500.0))  # only 5% drawdown

        assert rm._drawdown_halted is False

    # ── Consecutive-loss cooldown ─────────────────────────────────────────

    def test_consecutive_loss_pauses_strategy(self):
        rm = self._rm(max_consecutive_losses=3, loss_cooldown_bars=24)

        for _ in range(3):
            rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))

        sig = _make_signal(strategy="trend")
        passed, reason = rm.check_signal(sig, equity=10_000)
        assert passed is False
        assert "consecutive" in reason.lower()

    def test_win_resets_consecutive_loss_counter(self):
        rm = self._rm(max_consecutive_losses=3, loss_cooldown_bars=24)

        rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))
        rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))
        rm._on_fill(_make_fill(strategy="trend", pnl=+200.0))  # win resets counter
        rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))  # only 1 loss after reset

        passed, _ = rm.check_signal(_make_signal(strategy="trend"), equity=10_000)
        assert passed is True

    def test_cooldown_decrement_per_tick_bar(self):
        rm = self._rm(max_consecutive_losses=2, loss_cooldown_bars=4)

        for _ in range(2):
            rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))

        assert "trend" in rm._strategy_cooldown

        for _ in range(4):
            rm.tick_bar()

        assert "trend" not in rm._strategy_cooldown

    def test_other_strategy_not_affected_by_loss_cooldown(self):
        rm = self._rm(max_consecutive_losses=2, loss_cooldown_bars=24)

        for _ in range(2):
            rm._on_fill(_make_fill(strategy="trend", pnl=-100.0))

        # grid strategy is unaffected
        passed, _ = rm.check_signal(_make_signal(strategy="grid"), equity=10_000)
        assert passed is True

    # ── is_halted property ────────────────────────────────────────────────

    def test_is_halted_reflects_drawdown_state(self):
        rm = self._rm(max_drawdown_pct=10.0)
        rm.set_equity(10_000)
        rm._peak_equity = 10_000
        rm.set_equity(8_500)
        rm._on_fill(_make_fill(pnl=-1_500.0))

        assert rm.is_halted is True
