"""Unit tests for t1-dynamic-spacing.

Verifies that when `dynamic_spacing_enabled` is on, `_compute_dynamic_grid_count`
picks a count consistent with regime-aware spacing, and that the flag is OFF
by default (parity with legacy behaviour).
"""

from __future__ import annotations

from jackbot.core.constants import GridDirection, Regime
from jackbot.core.event_bus import EventBus
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig
from jackbot.strategy.market_assessor import MarketAssessment


def _assessment(regime: Regime, atr: float = 200.0) -> MarketAssessment:
    return MarketAssessment(
        symbol="BTCUSDT",
        direction=GridDirection.NEUTRAL,
        regime=regime,
        upper_price=97000.0,
        lower_price=93000.0,    # 4000 USDT range
        current_price=95000.0,
        adx=22.0,
        atr=atr,
        confidence=0.6,
        suggested_grid_count=10,
        suggested_leverage=8,
    )


class TestDynamicSpacing:
    def test_disabled_by_default(self):
        cfg = DayTraderConfig()
        assert cfg.dynamic_spacing_enabled is False

    def test_trending_uses_wider_spacing_than_ranging(self):
        """Trending k=0.8, ranging k=0.4 → trending spacing ≈ 2× ranging spacing
        → trending grid_count should be roughly half of ranging grid_count."""
        cfg = DayTraderConfig(dynamic_spacing_enabled=True)
        trader = DayTrader(config=cfg, event_bus=EventBus())

        n_trending = trader._compute_dynamic_grid_count(_assessment(Regime.TRENDING))
        n_neutral = trader._compute_dynamic_grid_count(_assessment(Regime.NEUTRAL))
        n_ranging = trader._compute_dynamic_grid_count(_assessment(Regime.RANGING))

        # 4000 / (200×0.8) = 25 ; 4000 / (200×0.6) = 33→cap30 ; 4000 / (200×0.4) = 50→cap30
        assert n_trending == 25
        assert n_neutral == 30
        assert n_ranging == 30
        assert n_trending < n_neutral <= n_ranging

    def test_clamped_to_min_count(self):
        """Tiny range / huge ATR → spacing larger than range → fall back to min_count."""
        cfg = DayTraderConfig(dynamic_spacing_enabled=True, dynamic_spacing_min_count=5)
        trader = DayTrader(config=cfg, event_bus=EventBus())
        # ATR 5000 in trending regime: spacing = 4000, range/spacing = 1 < 5 → min
        n = trader._compute_dynamic_grid_count(_assessment(Regime.TRENDING, atr=5000.0))
        assert n == 5

    def test_clamped_to_max_count(self):
        cfg = DayTraderConfig(dynamic_spacing_enabled=True, dynamic_spacing_max_count=30)
        trader = DayTrader(config=cfg, event_bus=EventBus())
        # ATR 50 in ranging regime: spacing = 20, range/spacing = 200 → cap to 30
        n = trader._compute_dynamic_grid_count(_assessment(Regime.RANGING, atr=50.0))
        assert n == 30

    def test_tick_size_floor_protects_from_zero_atr(self):
        cfg = DayTraderConfig(
            dynamic_spacing_enabled=True,
            dynamic_spacing_tick_size=10.0,    # spacing floor 50 USDT
            dynamic_spacing_max_count=30,
        )
        trader = DayTrader(config=cfg, event_bus=EventBus())
        # ATR=0 → would div by zero without floor; with floor=50, range 4000 → 80 → cap 30
        n = trader._compute_dynamic_grid_count(_assessment(Regime.NEUTRAL, atr=0.0))
        assert n == 30
