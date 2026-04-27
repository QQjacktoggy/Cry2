"""Unit tests for t3-fill-model-upgrade.

Tests target the pure-function pieces of the upgraded backtest fill model:
  * FillSimulator volume-aware fill ratio (and legacy back-compat)
  * Multi-level distance-to-close ordering of fills
  * funding_rate_at bisect lookup
  * FillSimulator.compute_funding sign for net long / short positions
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_backtest():
    spec = importlib.util.spec_from_file_location(
        "bt", str(ROOT / "scripts" / "backtest.py"),
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bt = _load_backtest()
from jackbot.core.constants import GridDirection, GridLevelState  # noqa: E402
from jackbot.core.events import MarketEvent  # noqa: E402


def _bar(close: float, low: float, high: float, volume: float = 100.0) -> MarketEvent:
    return MarketEvent(
        timestamp=datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="5m",
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source="backtest",
    )


class TestFillRatio:
    def test_legacy_fill_uses_random_uniform(self):
        sim = bt.FillSimulator(legacy_fill=True)
        # legacy returns something in [0.5, 1.0]
        for _ in range(20):
            r = sim._fill_ratio(level_qty=0.001, bar_volume=10.0)
            assert 0.5 <= r <= 1.0

    def test_volume_aware_clamped_low(self):
        sim = bt.FillSimulator(legacy_fill=False, volume_norm=50.0)
        # very low volume → clamped to 0.1
        r = sim._fill_ratio(level_qty=10.0, bar_volume=0.0)
        assert r == 0.1

    def test_volume_aware_clamped_high(self):
        sim = bt.FillSimulator(legacy_fill=False, volume_norm=50.0)
        # huge volume → clamped to 1.0
        r = sim._fill_ratio(level_qty=0.001, bar_volume=1_000_000.0)
        assert r == 1.0

    def test_volume_aware_proportional_in_band(self):
        sim = bt.FillSimulator(legacy_fill=False, volume_norm=50.0)
        # qty=0.01, vol=10 → 10/0.01/50 = 20 → clamped to 1.0
        # qty=0.01, vol=2.5 → 2.5/0.01/50 = 5 → clamped to 1.0
        # qty=0.01, vol=0.1 → 0.1/0.01/50 = 0.2 → 0.2 (in band)
        r = sim._fill_ratio(level_qty=0.01, bar_volume=0.1)
        assert abs(r - 0.2) < 1e-9


class TestDistanceOrdering:
    def test_levels_sorted_by_distance_to_close(self):
        engine_module = sys.modules.get("jackbot.strategy.grid_engine")
        if engine_module is None:
            from jackbot.strategy import grid_engine as engine_module
        eng = engine_module.GridEngine()
        grid, _ = eng.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.NEUTRAL,
            upper_price=96000.0,
            lower_price=94000.0,
            grid_count=10,
            leverage=10,
            total_investment=75.0,
            current_price=95000.0,
        )
        # Force three pending levels into a state where they are ALL in-bar
        for lvl in grid.levels[3:6]:
            lvl.state = GridLevelState.PENDING_BUY
        sim = bt.FillSimulator(legacy_fill=True)    # avoid randomness in qty
        bar = _bar(close=95000.0, low=94000.0, high=96000.0)
        fills = sim.check_fills([grid], bar)
        # Sorted by |level.price - close|: levels closest to 95000 first
        prices = [f.price for f in fills]
        sorted_by_dist = sorted(prices, key=lambda p: abs(p - bar.close))
        assert prices == sorted_by_dist


class TestFundingRateAt:
    def test_returns_zero_before_first_event(self):
        events = [(1000, 0.0001), (2000, 0.0002)]
        assert bt.funding_rate_at(events, 500) == 0.0

    def test_returns_event_at_exact_ts(self):
        events = [(1000, 0.0001), (2000, 0.0002)]
        assert bt.funding_rate_at(events, 1000) == 0.0001

    def test_returns_most_recent_event_in_band(self):
        events = [(1000, 0.0001), (2000, 0.0002), (3000, -0.0001)]
        assert bt.funding_rate_at(events, 2500) == 0.0002

    def test_returns_last_event_after_window(self):
        events = [(1000, 0.0001), (2000, 0.0002)]
        assert bt.funding_rate_at(events, 9999) == 0.0002

    def test_empty_events(self):
        assert bt.funding_rate_at([], 1000) == 0.0


class TestComputeFunding:
    def test_long_position_pays_positive_rate(self):
        from jackbot.strategy import grid_engine
        eng = grid_engine.GridEngine()
        grid, _ = eng.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.LONG,
            upper_price=96000.0,
            lower_price=94000.0,
            grid_count=10,
            leverage=10,
            total_investment=75.0,
            current_price=95000.0,
        )
        # Hand-set a held long
        level = grid.levels[3]
        level.state = GridLevelState.FILLED_BUY
        level.buy_fill_price = 95000.0
        level.quantity = 0.01

        sim = bt.FillSimulator()
        # rate +0.0001 → cost 0.01 × 95000 × 0.0001 = 0.095 USDT (positive = cost)
        charge = sim.compute_funding(grid, current_price=95000.0, rate=0.0001)
        assert abs(charge - 0.095) < 1e-9

    def test_short_position_receives_positive_rate(self):
        from jackbot.strategy import grid_engine
        eng = grid_engine.GridEngine()
        grid, _ = eng.create_grid(
            symbol="BTCUSDT",
            direction=GridDirection.SHORT,
            upper_price=96000.0,
            lower_price=94000.0,
            grid_count=10,
            leverage=10,
            total_investment=75.0,
            current_price=95000.0,
        )
        level = grid.levels[7]
        level.state = GridLevelState.FILLED_SELL
        level.sell_fill_price = 95000.0
        level.quantity = 0.01

        sim = bt.FillSimulator()
        # net long notional is negative for a short → charge negative (credit)
        charge = sim.compute_funding(grid, current_price=95000.0, rate=0.0001)
        assert charge < 0
