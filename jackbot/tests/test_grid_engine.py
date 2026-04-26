"""Unit tests for GridEngine."""

from datetime import UTC, datetime

from jackbot.core.constants import GridDirection, GridLevelState, OrderSide
from jackbot.core.events import FillEvent
from jackbot.strategy.grid_engine import GridEngine


def _now():
    return datetime.now(UTC)


class TestGridCreation:
    """Test grid level calculation and initial order generation."""

    def test_arithmetic_grid_levels(self):
        engine = GridEngine()
        grid, signals = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # 10 intervals → 11 levels (including both boundaries)
        assert len(grid.levels) == 11
        assert grid.levels[0].price == 90.0
        assert grid.levels[10].price == 100.0

        # Step should be 1.0
        step = grid.levels[1].price - grid.levels[0].price
        assert abs(step - 1.0) < 0.01

    def test_neutral_grid_initial_orders(self):
        engine = GridEngine()
        grid, signals = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        buy_signals = [s for s in signals if s.side == "BUY"]
        sell_signals = [s for s in signals if s.side == "SELL"]

        # Levels below 95 → BUY, levels above 95 → SELL
        assert len(buy_signals) > 0
        assert len(sell_signals) > 0

        # All BUY prices should be < 95
        for s in buy_signals:
            assert s.price < 95.0

        # All SELL prices should be > 95
        for s in sell_signals:
            assert s.price > 95.0

    def test_long_grid_only_buys_below(self):
        engine = GridEngine()
        grid, signals = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.LONG,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # Long grid: only BUY orders below current price
        for s in signals:
            assert s.side == "BUY"
            assert s.price < 95.0

    def test_short_grid_only_sells_above(self):
        engine = GridEngine()
        grid, signals = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.SHORT,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        for s in signals:
            assert s.side == "SELL"
            assert s.price > 95.0

    def test_per_level_quantity(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # per_level_qty = (100/10) * 10 / 95 ≈ 1.0526
        expected = (100.0 / 10) * 10 / 95.0
        assert abs(grid.per_level_qty - round(expected, 6)) < 0.01


class TestGridFills:
    """Test fill handling and counter-order placement."""

    def test_buy_fill_triggers_sell(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # Simulate BUY fill at level 2 (price=92.0)
        fill = FillEvent(
            timestamp=_now(),
            symbol="BTCUSDT",
            side="BUY",
            quantity=grid.per_level_qty,
            price=92.0,
            grid_id=grid.grid_id,
            level_index=2,
        )

        counter_signals, profit = engine.on_fill(fill)

        # Should place a SELL at level 3
        assert len(counter_signals) == 1
        assert counter_signals[0].side == "SELL"
        assert counter_signals[0].level_index == 3
        assert profit is None  # No profit yet

    def test_matched_profit_on_sell(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        qty = grid.per_level_qty

        # Step 1: BUY fills at level 2
        buy_fill = FillEvent(
            timestamp=_now(),
            symbol="BTCUSDT",
            side="BUY",
            quantity=qty,
            price=92.0,
            grid_id=grid.grid_id,
            level_index=2,
        )
        engine.on_fill(buy_fill)

        # Step 2: SELL fills at level 3 (counter-order)
        sell_fill = FillEvent(
            timestamp=_now(),
            symbol="BTCUSDT",
            side="SELL",
            quantity=qty,
            price=93.0,
            grid_id=grid.grid_id,
            level_index=3,
        )
        counter, profit_event = engine.on_fill(sell_fill)

        # Should have profit: (93 - 92) * qty
        assert profit_event is not None
        expected_profit = (93.0 - 92.0) * qty
        assert abs(profit_event.profit_usd - expected_profit) < 0.01

        # Should re-place BUY at level 2
        assert len(counter) == 1
        assert counter[0].side == "BUY"
        assert counter[0].level_index == 2

        # Grid total matched profit should be updated
        assert grid.matched_profit > 0
        assert grid.total_matched == 1


class TestGridBreakout:
    """Test breakout detection."""

    def test_upper_breakout_detected(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # Price way above upper
        breakouts = engine.check_breakout("BTCUSDT", 101.0)
        assert len(breakouts) == 1
        assert breakouts[0] == grid.grid_id

    def test_no_breakout_within_range(self):
        engine = GridEngine()
        engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        breakouts = engine.check_breakout("BTCUSDT", 96.0)
        assert len(breakouts) == 0


class TestGridClose:
    """Test grid closure."""

    def test_close_grid(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        engine.close_grid(grid.grid_id, reason="test")

        assert grid.closed
        assert grid.close_reason == "test"
        assert len(engine.active_grids) == 0


class TestUnrealizedPnL:
    """Test unrealized PnL calculation and stop-loss."""

    def _create_grid_with_fills(self):
        """Helper: create a grid and simulate a BUY fill at level 2."""
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # Simulate BUY fill at level 2 (price=92.0)
        fill = FillEvent(
            timestamp=_now(),
            symbol="BTCUSDT",
            side="BUY",
            quantity=grid.per_level_qty,
            price=92.0,
            grid_id=grid.grid_id,
            level_index=2,
        )
        engine.on_fill(fill)
        return engine, grid

    def test_positive_unrealized_pnl(self):
        engine, grid = self._create_grid_with_fills()

        # Price went up → positive PnL
        engine.update_unrealized_pnl("BTCUSDT", 94.0)
        assert grid.unrealized_pnl > 0
        # Expect roughly (94 - 92) * qty
        expected = (94.0 - 92.0) * grid.per_level_qty
        assert abs(grid.unrealized_pnl - expected) < 0.01

    def test_negative_unrealized_pnl(self):
        engine, grid = self._create_grid_with_fills()

        # Price went down → negative PnL
        engine.update_unrealized_pnl("BTCUSDT", 88.0)
        assert grid.unrealized_pnl < 0

    def test_stop_loss_triggered(self):
        engine, grid = self._create_grid_with_fills()

        # Simulate big loss: price dropped to 80
        engine.update_unrealized_pnl("BTCUSDT", 80.0)

        # With 100 USDT investment, the loss % should exceed 2%
        stopped = engine.check_stop_loss("BTCUSDT", stop_loss_pct=2.0)
        assert len(stopped) == 1
        assert stopped[0] == grid.grid_id

    def test_stop_loss_not_triggered_within_threshold(self):
        engine, grid = self._create_grid_with_fills()

        # Small loss: price at 91.5 (bought at 92)
        engine.update_unrealized_pnl("BTCUSDT", 91.5)

        # Loss is (92-91.5)*qty ≈ 0.53, on 100 investment = 0.53% < 2%
        stopped = engine.check_stop_loss("BTCUSDT", stop_loss_pct=2.0)
        assert len(stopped) == 0

    def test_unrealized_pnl_for_short_position(self):
        engine = GridEngine()
        grid, _ = engine.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.SHORT,
            upper_price=100.0,
            lower_price=90.0,
            grid_count=10,
            leverage=10,
            total_investment=100.0,
            current_price=95.0,
        )

        # Simulate SELL fill at level 7 (price=97.0)
        level = grid.levels[7]
        level.state = GridLevelState.FILLED_SELL
        level.sell_fill_price = 97.0

        # Price dropped → profit on short
        engine.update_unrealized_pnl("BTCUSDT", 93.0)
        assert grid.unrealized_pnl > 0  # (97 - 93) * qty > 0

        # Price went up → loss on short
        engine.update_unrealized_pnl("BTCUSDT", 99.0)
        assert grid.unrealized_pnl < 0  # (97 - 99) * qty < 0

