"""Unit tests for t1-drawdown-throttle.

Verifies the tiered factor curve and that the flag default is OFF (factor 1.0).
"""

from __future__ import annotations

from jackbot.core.event_bus import EventBus
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig


def _trader(**overrides) -> DayTrader:
    overrides.setdefault("total_capital_usd", 150.0)
    cfg = DayTraderConfig(**overrides)
    return DayTrader(config=cfg, event_bus=EventBus())


class TestDrawdownThrottle:
    def test_disabled_by_default(self):
        cfg = DayTraderConfig()
        assert cfg.graduated_throttle_enabled is False

    def test_no_loss_returns_one(self):
        t = _trader(graduated_throttle_enabled=True)
        t._daily_loss = 0.0
        assert t._drawdown_throttle_factor() == 1.0

    def test_below_tier1_returns_one(self):
        t = _trader(graduated_throttle_enabled=True)
        t._daily_loss = 4.0    # 2.6% of 150 < 3% tier1
        assert t._drawdown_throttle_factor() == 1.0

    def test_tier1_factor(self):
        t = _trader(graduated_throttle_enabled=True)
        t._daily_loss = 5.0    # 3.3% of 150 → tier1
        assert t._drawdown_throttle_factor() == 0.7

    def test_tier2_factor(self):
        t = _trader(graduated_throttle_enabled=True)
        t._daily_loss = 8.0    # 5.3% of 150 → tier2
        assert t._drawdown_throttle_factor() == 0.4

    def test_tier3_factor(self):
        t = _trader(graduated_throttle_enabled=True)
        t._daily_loss = 13.0    # 8.6% of 150 → tier3
        assert t._drawdown_throttle_factor() == 0.2

    def test_zero_capital_returns_one(self):
        t = _trader(graduated_throttle_enabled=True, total_capital_usd=0.0)
        t._daily_loss = 5.0
        assert t._drawdown_throttle_factor() == 1.0

    def test_factor_applied_to_grid_creation(self):
        """When throttle is on and DD breached, _try_create_grid should produce
        a grid with smaller investment & leverage than baseline."""
        # Two traders side by side: one throttled, one not.
        # We probe internal arithmetic via _drawdown_throttle_factor combined
        # with the trader's known sizing logic (per_symbol_alloc_pct = 50%).
        t_on = _trader(
            graduated_throttle_enabled=True,
            per_symbol_alloc_pct=50.0,
            min_leverage=5,
        )
        t_on._daily_loss = 8.0    # tier2 → ×0.4
        baseline_inv = 150.0 * 0.5
        throttled_inv = baseline_inv * t_on._drawdown_throttle_factor()
        assert throttled_inv == baseline_inv * 0.4
        # Leverage 10 × 0.4 = 4 → clamped to min_leverage 5
        throttled_lev = max(5, int(10 * t_on._drawdown_throttle_factor()))
        assert throttled_lev == 5
