"""Unit tests for t1-partial-tp-trailing.

Covers:
- partial TP fires once when floating PnL crosses threshold (long & short)
- trailing exit fires when price retraces from high-water-mark by ATR×k
- flag OFF preserves legacy behaviour (no signals emitted)
"""

from __future__ import annotations

from jackbot.core.constants import GridDirection, GridLevelState, OrderSide
from jackbot.strategy.grid_engine import GridEngine


def _setup_long_grid_with_filled_level(
    engine: GridEngine,
    *,
    partial_tp_enabled: bool = True,
    partial_tp_pct: float = 1.0,
    trailing_atr_mult: float = 0.5,
    buy_price: float = 95000.0,
    qty: float = 0.01,
):
    grid, _ = engine.create_grid(
        symbol="BTCUSDT",
        direction=GridDirection.LONG,
        upper_price=96000.0,
        lower_price=94000.0,
        grid_count=10,
        leverage=10,
        total_investment=75.0,
        current_price=95000.0,
        partial_tp_enabled=partial_tp_enabled,
        partial_tp_pct=partial_tp_pct,
        partial_tp_close_pct=0.5,
        trailing_atr_mult=trailing_atr_mult,
    )
    # Hand-set level 3 as a filled long position
    level = grid.levels[3]
    level.state = GridLevelState.FILLED_BUY
    level.buy_fill_price = buy_price
    level.quantity = qty
    return grid, level


class TestPartialTPLong:
    def test_no_signals_when_disabled(self):
        engine = GridEngine()
        grid, level = _setup_long_grid_with_filled_level(engine, partial_tp_enabled=False)
        signals, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 96500.0, atr=200.0)
        assert signals == []
        assert exits == []
        assert level.trailing_active is False
        assert level.quantity == 0.01    # untouched

    def test_no_signals_below_threshold(self):
        engine = GridEngine()
        _, level = _setup_long_grid_with_filled_level(engine, partial_tp_pct=1.0)
        # +0.5% move only — below 1.0% threshold
        signals, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 95475.0, atr=200.0)
        assert signals == []
        assert exits == []
        assert level.trailing_active is False

    def test_partial_tp_triggers_on_threshold(self):
        engine = GridEngine()
        grid, level = _setup_long_grid_with_filled_level(engine, partial_tp_pct=1.0)
        # +1.05% move
        signals, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 96000.0, atr=200.0)
        assert exits == []
        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL.value
        assert sig.order_type == "MARKET"
        assert sig.reduce_only is True
        assert sig.quantity == round(0.01 * 0.5, 6)
        assert level.trailing_active is True
        assert level.high_water_mark == 96000.0
        assert level.quantity == round(0.01 - 0.005, 6)
        # partial TP profit = (96000 - 95000) × 0.005 = 5.0 USDT
        assert grid.partial_tp_profit == 5.0
        assert grid.matched_profit == 5.0

    def test_partial_tp_fires_only_once(self):
        engine = GridEngine()
        _, level = _setup_long_grid_with_filled_level(engine, partial_tp_pct=1.0)
        # First trigger
        s1, _ = engine.check_partial_tp_and_trailing("BTCUSDT", 96000.0, atr=200.0)
        assert len(s1) == 1
        # Even higher price → no second partial TP, just water-mark update
        s2, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 96500.0, atr=200.0)
        assert s2 == []
        assert exits == []
        assert level.high_water_mark == 96500.0

    def test_trailing_exit_fires_on_retrace(self):
        engine = GridEngine()
        _, level = _setup_long_grid_with_filled_level(
            engine, partial_tp_pct=1.0, trailing_atr_mult=0.5,
        )
        # Trigger partial TP
        engine.check_partial_tp_and_trailing("BTCUSDT", 96000.0, atr=200.0)
        # Push hwm up
        engine.check_partial_tp_and_trailing("BTCUSDT", 96400.0, atr=200.0)
        assert level.high_water_mark == 96400.0
        # Retrace > ATR×0.5 = 100 USDT → 96400 - 100 = 96300; price 96250 < 96300 → exit
        signals, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 96250.0, atr=200.0)
        assert signals == []      # no extra partial-close signal
        assert len(exits) == 1    # caller should close the whole grid


class TestPartialTPShort:
    def test_short_partial_tp(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.SHORT,
            upper_price=96000.0,
            lower_price=94000.0,
            grid_count=10,
            leverage=10,
            total_investment=75.0,
            current_price=95000.0,
            partial_tp_enabled=True,
            partial_tp_pct=1.0,
            partial_tp_close_pct=0.5,
            trailing_atr_mult=0.5,
        )
        # Hand-set a filled short
        level = grid.levels[7]
        level.state = GridLevelState.FILLED_SELL
        level.sell_fill_price = 95000.0
        level.quantity = 0.01

        # Price falls 1% → +1% PnL on short → partial TP triggers (BUY-side reduce-only)
        signals, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 94000.0, atr=200.0)
        assert exits == []
        assert len(signals) == 1
        assert signals[0].side == OrderSide.BUY.value
        assert signals[0].reduce_only is True
        assert level.trailing_active is True

        # Price rebounds past hwm + 100 → trailing exit
        _, exits = engine.check_partial_tp_and_trailing("BTCUSDT", 94250.0, atr=200.0)
        assert len(exits) == 1
