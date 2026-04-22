"""Reduce-only signals must pass RiskManager even when halted."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.core.constants import OrderSide, OrderType
from bot.core.event_bus import EventBus
from bot.core.events import SignalEvent
from bot.risk.risk_manager import RiskManager


def _signal(reduce_only: bool) -> SignalEvent:
    return SignalEvent(
        timestamp=datetime.now(UTC),
        strategy_name="v74_test",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=0.001,
        price=30_000.0,
        reduce_only=reduce_only,
    )


def test_reduce_only_passes_through_drawdown_halt() -> None:
    bus = EventBus()
    rm = RiskManager(event_bus=bus, max_drawdown_pct=5.0, drawdown_cooldown_bars=10)
    rm.set_equity(100.0)
    # Simulate peak then drawdown.
    rm._peak_equity = 100.0
    rm._equity = 94.0
    rm._drawdown_halted = True
    rm._drawdown_halt_bars_remaining = 10

    passed_exit, reason_exit = rm.check_signal(_signal(reduce_only=True), 94.0)
    assert passed_exit, reason_exit

    passed_open, reason_open = rm.check_signal(_signal(reduce_only=False), 94.0)
    assert not passed_open
    assert "reduce-only" in reason_open.lower()


def test_reduce_only_passes_through_daily_halt() -> None:
    bus = EventBus()
    rm = RiskManager(event_bus=bus)
    rm.set_equity(100.0)
    rm._daily_halted = True

    passed_exit, _ = rm.check_signal(_signal(reduce_only=True), 100.0)
    passed_open, _ = rm.check_signal(_signal(reduce_only=False), 100.0)
    assert passed_exit
    assert not passed_open
