"""Unit tests for t1-maker-only-close.

Verifies that with maker_only_close=True, close_grid emits post-only LIMIT
reduce-only orders for held positions, in addition to the cancel signals it
already emits for pending orders. With the flag OFF, behaviour matches legacy.
"""

from __future__ import annotations

from jackbot.core.constants import GridDirection, GridLevelState
from jackbot.strategy.grid_engine import GridEngine


def _grid_with_filled_long(engine: GridEngine, *, maker_only_close: bool):
    grid, _ = engine.create_grid(
        symbol="BTCUSDT",
        direction=GridDirection.LONG,
        upper_price=96000.0,
        lower_price=94000.0,
        grid_count=10,
        leverage=10,
        total_investment=75.0,
        current_price=95000.0,
        maker_only_close=maker_only_close,
        maker_close_tick_size=0.1,
    )
    # Hand-set level 3 as a held long position
    level = grid.levels[3]
    level.state = GridLevelState.FILLED_BUY
    level.buy_fill_price = 94600.0
    level.buy_order_id = "ord-buy-3"
    level.quantity = 0.01
    return grid, level


class TestMakerOnlyCloseDisabled:
    def test_legacy_close_emits_no_position_signal(self):
        engine = GridEngine()
        _, _ = _grid_with_filled_long(engine, maker_only_close=False)
        signals = engine.close_grid(list(engine._grids.keys())[0], reason="stop_loss")
        # Legacy: only emits cancel signals for pending orders. Filled levels
        # have no pending order_id, so this grid produces zero signals.
        assert all(s.cancel_order_id != "" or s.order_type == "MARKET" for s in signals)
        # No reduce_only LIMITs should be emitted
        limits = [s for s in signals if s.order_type == "LIMIT" and s.reduce_only]
        assert limits == []


class TestMakerOnlyCloseEnabled:
    def test_emits_post_only_sell_for_filled_long(self):
        engine = GridEngine()
        _, level = _grid_with_filled_long(engine, maker_only_close=True)
        grid_id = list(engine._grids.keys())[0]
        signals = engine.close_grid(grid_id, reason="stop_loss", current_price=95000.0)

        close_limits = [s for s in signals if s.order_type == "LIMIT" and s.reduce_only]
        assert len(close_limits) == 1
        sig = close_limits[0]
        assert sig.side == "SELL"               # closes the long
        assert sig.price == round(95000.0 + 0.1, 2)
        assert sig.quantity == level.quantity
        assert sig.metadata.get("close_intent") == "maker_only"
        assert sig.metadata.get("fallback_after_s") == 5

    def test_emits_post_only_buy_for_filled_short(self):
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
            maker_only_close=True,
            maker_close_tick_size=0.1,
        )
        level = grid.levels[7]
        level.state = GridLevelState.FILLED_SELL
        level.sell_fill_price = 95400.0
        level.sell_order_id = "ord-sell-7"
        level.quantity = 0.01

        signals = engine.close_grid(grid.grid_id, reason="stop_loss", current_price=95000.0)
        close_limits = [s for s in signals if s.order_type == "LIMIT" and s.reduce_only]
        assert len(close_limits) == 1
        sig = close_limits[0]
        assert sig.side == "BUY"                # closes the short
        assert sig.price == round(95000.0 - 0.1, 2)

    def test_falls_back_to_level_price_when_no_current_price(self):
        engine = GridEngine()
        _, level = _grid_with_filled_long(engine, maker_only_close=True)
        grid_id = list(engine._grids.keys())[0]
        signals = engine.close_grid(grid_id, reason="stop_loss")  # current_price=0.0
        close_limits = [s for s in signals if s.order_type == "LIMIT" and s.reduce_only]
        assert len(close_limits) == 1
        # uses level.price + tick when current_price not provided
        assert close_limits[0].price == round(level.price + 0.1, 2)
