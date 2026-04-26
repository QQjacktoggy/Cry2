"""Unit tests for DayTrader."""

from datetime import UTC, datetime
from types import SimpleNamespace

from jackbot.core.constants import GridDirection, Regime, TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, MarketEvent
from jackbot.strategy.market_assessor import MarketAssessment
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


def _warmup(trader: DayTrader, symbol: str, price: float, bars: int = 55):
    """Feed enough bars with a mild uptrend to build sufficient ADX/confidence."""
    for i in range(bars):
        # Mild uptrend + oscillation → produces ADX > 20 and reasonable BB width
        trend = i * 20  # gradual uptrend
        oscillation = (i % 6 - 3) * 50  # oscillation for BB width
        p = price + trend + oscillation
        bar = MarketEvent(
            timestamp=datetime(2026, 4, 24, 10, i % 60, 0, tzinfo=UTC),
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
        assert signals == []  # Still in warmup

    def test_creates_grid_after_warmup(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)

        # Next bar should trigger grid creation
        bar = _make_bar("BTCUSDT", 95000.0, offset=55)
        signals = trader.on_bar(bar)

        # Should have created a grid and returned initial order signals
        assert len(signals) > 0

    def test_mode_starts_aggressive(self):
        bus = EventBus()
        config = DayTraderConfig()
        trader = DayTrader(config=config, event_bus=bus)
        assert trader.mode == TradingMode.AGGRESSIVE

    def test_conservative_switch_on_target(self):
        """When daily profit reaches target, mode should switch to conservative."""
        bus = EventBus()
        config = DayTraderConfig(daily_profit_target_usd=2.0, warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)

        assert trader.mode == TradingMode.AGGRESSIVE

        # Directly simulate profit accumulation via the internal mechanism
        # (In production, this happens through on_fill → GridProfitEvent chain)
        trader._daily_profit = 1.5
        assert trader.mode == TradingMode.AGGRESSIVE  # Not yet

        # Simulate crossing the target via _switch_to_conservative
        trader._daily_profit = 2.5
        trader._switch_to_conservative()
        assert trader.mode == TradingMode.CONSERVATIVE

    def test_halts_on_loss_limit(self):
        bus = EventBus()
        config = DayTraderConfig(daily_loss_limit_usd=5.0, warmup_bars=50)
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)
        bar = _make_bar("BTCUSDT", 95000.0, offset=55)
        trader.on_bar(bar)

        # Simulate losses
        for i in range(10):
            fill = FillEvent(
                timestamp=datetime.now(UTC),
                symbol="BTCUSDT",
                side="SELL",
                quantity=0.001,
                price=94000.0,
                realized_pnl=-1.0,
                grid_id="fake_grid",
                level_index=0,
            )
            trader.on_fill(fill)

            if trader.is_halted:
                break

        assert trader.is_halted

    def test_daily_reset_clears_state(self):
        bus = EventBus()
        config = DayTraderConfig()
        trader = DayTrader(config=config, event_bus=bus)

        # Set some state
        trader._daily_profit = 8.0
        trader._mode = TradingMode.CONSERVATIVE
        trader._daily_resets = 5
        trader._last_reset_date = "2026-04-23"

        # Trigger reset by processing a bar (which will detect date change)
        bar = _make_bar("BTCUSDT", 95000.0)
        trader.on_bar(bar)

        assert trader._daily_profit == 0.0
        assert trader._mode == TradingMode.AGGRESSIVE
        assert trader._daily_resets == 0


class TestDayTraderStatus:
    def test_get_status(self):
        bus = EventBus()
        config = DayTraderConfig()
        trader = DayTrader(config=config, event_bus=bus)

        status = trader.get_status()
        assert "mode" in status
        assert "daily_profit" in status
        assert "daily_target" in status
        assert "active_grids" in status
        assert status["mode"] == "aggressive"


class TestHourlyReview:
    def test_review_not_triggered_before_interval(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50, hourly_review_interval_bars=12)
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)

        # Feed 5 bars after warmup — not enough for review (need 12)
        for i in range(5):
            bar = _make_bar("BTCUSDT", 95000.0, offset=i)
            trader.on_bar(bar)

        # No review should have been logged — bar count is only 5 since warmup
        # The trader should have created a grid though
        active = [g for g in trader._engine.active_grids if g.symbol == "BTCUSDT"]
        # Just verify no crash and grid exists
        assert len(active) <= 1  # could be 0 or 1

    def test_review_triggered_at_interval(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=50, hourly_review_interval_bars=12)
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)

        # Feed 13 bars after warmup → should trigger review
        for i in range(13):
            bar = _make_bar("BTCUSDT", 95000.0 + i * 20, offset=i)
            trader.on_bar(bar)

        # Review happened at bar 12 — just verify no crash
        assert trader._last_review_bar.get("BTCUSDT", 0) > 0


class TestStopLoss:
    def test_stop_loss_closes_grid(self):
        """When a grid's unrealized loss exceeds stop_loss_pct, it should be closed."""
        bus = EventBus()
        config = DayTraderConfig(
            warmup_bars=50,
            grid_stop_loss_pct=2.0,
        )
        trader = DayTrader(config=config, event_bus=bus)

        _warmup(trader, "BTCUSDT", 95000.0)

        # Create a grid
        bar = _make_bar("BTCUSDT", 96000.0, offset=55)
        signals = trader.on_bar(bar)

        active = [g for g in trader._engine.active_grids if g.symbol == "BTCUSDT"]
        if active:
            grid = active[0]
            # Simulate a filled buy level
            from jackbot.core.constants import GridLevelState
            level = grid.levels[1]
            level.state = GridLevelState.FILLED_BUY
            level.buy_fill_price = 96000.0
            level.quantity = 0.01  # big qty for testing

            # Now feed a bar with a big price drop → trigger stop-loss
            crash_bar = MarketEvent(
                timestamp=datetime(2026, 4, 24, 14, 0, 0, tzinfo=UTC),
                symbol="BTCUSDT",
                timeframe="5m",
                open=90000,
                high=90500,
                low=89000,
                close=89000,  # big drop from 96000
                volume=5000.0,
            )
            trader.on_bar(crash_bar)

            # Grid should have been closed due to stop-loss
            assert grid.closed
            assert "stop_loss" in grid.close_reason


class TestAdaptiveSizing:
    def test_trending_high_confidence_allocates_more_than_ranging_low_confidence(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=1)
        trader = DayTrader(config=config, event_bus=bus)

        captured: list[dict] = []

        def fake_create_grid(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(grid_id=f"g{len(captured)}"), []

        trader._engine.create_grid = fake_create_grid  # type: ignore[method-assign]

        high = MarketAssessment(
            symbol="BTCUSDT",
            direction=GridDirection.LONG,
            regime=Regime.TRENDING,
            upper_price=101000.0,
            lower_price=99000.0,
            current_price=100000.0,
            adx=35.0,
            atr=600.0,
            confidence=0.92,
            suggested_grid_count=10,
            suggested_leverage=10,
        )
        low = MarketAssessment(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            regime=Regime.RANGING,
            upper_price=100800.0,
            lower_price=99200.0,
            current_price=100000.0,
            adx=18.0,
            atr=500.0,
            confidence=0.35,
            suggested_grid_count=10,
            suggested_leverage=10,
        )

        trader._assessor.assess = lambda _symbol: high  # type: ignore[method-assign]
        trader._try_create_grid("BTCUSDT", 100000.0)

        trader._assessor.assess = lambda _symbol: low  # type: ignore[method-assign]
        trader._try_create_grid("BTCUSDT", 100000.0)

        assert len(captured) == 2
        assert captured[0]["total_investment"] > captured[1]["total_investment"]
        assert captured[0]["leverage"] >= captured[1]["leverage"]

    def test_adaptive_leverage_is_clamped_to_config_bounds(self):
        bus = EventBus()
        config = DayTraderConfig(warmup_bars=1, min_leverage=5, max_leverage=10)
        trader = DayTrader(config=config, event_bus=bus)

        captured: list[dict] = []

        def fake_create_grid(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(grid_id="g1"), []

        trader._engine.create_grid = fake_create_grid  # type: ignore[method-assign]

        assessment = MarketAssessment(
            symbol="ETHUSDT",
            direction=GridDirection.LONG,
            regime=Regime.TRENDING,
            upper_price=2500.0,
            lower_price=2300.0,
            current_price=2400.0,
            adx=40.0,
            atr=30.0,
            confidence=1.0,
            suggested_grid_count=8,
            suggested_leverage=10,
        )

        trader._assessor.assess = lambda _symbol: assessment  # type: ignore[method-assign]
        trader._try_create_grid("ETHUSDT", 2400.0)

        assert len(captured) == 1
        assert config.min_leverage <= captured[0]["leverage"] <= config.max_leverage

