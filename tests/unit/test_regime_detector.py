"""Tests for D2 RegimeDetector and D3 ATR-adaptive leverage in RiskManager."""

from __future__ import annotations

import math

import pytest

from bot.core.event_bus import EventBus
from bot.risk.regime_detector import Regime, RegimeDetector
from bot.risk.risk_manager import RiskManager


# ── RegimeDetector unit tests ────────────────────────────────────────────────


class TestRegimeDetector:
    def test_neutral_before_warmup(self):
        rd = RegimeDetector(period=14)
        # Fewer than period bars → still NEUTRAL
        for i in range(5):
            rd.update(100 + i, 99 + i, 100 + i)
        assert rd.regime == Regime.NEUTRAL
        assert rd.adx == 0.0

    def test_threshold_validation(self):
        with pytest.raises(ValueError):
            RegimeDetector(trending_threshold=20.0, ranging_threshold=20.0)
        with pytest.raises(ValueError):
            RegimeDetector(trending_threshold=15.0, ranging_threshold=20.0)

    def _feed_trending(self, rd: RegimeDetector, n: int = 60) -> None:
        """Feed strongly trending bars (consistent up-moves) to push ADX high."""
        price = 100.0
        for i in range(n):
            high = price + 2.0
            low = price - 0.5
            close = price + 1.5
            rd.update(high, low, close)
            price += 1.5

    def _feed_ranging(self, rd: RegimeDetector, n: int = 60) -> None:
        """Feed choppy bars (alternating direction) to push ADX low."""
        price = 100.0
        direction = 1
        for _ in range(n):
            high = price + 0.3
            low = price - 0.3
            close = price + 0.1 * direction
            rd.update(high, low, close)
            direction *= -1

    def test_trending_regime_after_strong_trend(self):
        rd = RegimeDetector(period=14, trending_threshold=25.0, ranging_threshold=20.0)
        self._feed_trending(rd, n=60)
        # Strong consistent trend → ADX should exceed 25
        assert rd.adx > 25.0
        assert rd.regime == Regime.TRENDING
        assert rd.is_trending is True
        assert rd.is_ranging is False

    def test_ranging_regime_after_choppy_bars(self):
        rd = RegimeDetector(period=14, trending_threshold=25.0, ranging_threshold=20.0)
        self._feed_ranging(rd, n=80)
        # Choppy market → ADX should drop below 20
        assert rd.adx < 20.0
        assert rd.regime == Regime.RANGING
        assert rd.is_ranging is True
        assert rd.is_trending is False

    def test_regime_transitions(self):
        # Ranging first (ADX stays low), then trending pushes ADX above threshold.
        # This direction is reliable because ADX rises quickly with strong moves.
        rd = RegimeDetector(period=14, trending_threshold=25.0, ranging_threshold=20.0)
        self._feed_ranging(rd, n=80)
        assert rd.regime == Regime.RANGING
        # Now feed a strong trend — ADX should eventually cross 25
        self._feed_trending(rd, n=60)
        assert rd.regime == Regime.TRENDING

    def test_adx_positive_after_trending_bars(self):
        rd = RegimeDetector(period=14)
        price = 100.0
        for _ in range(30):
            high = price + 2.0
            low = price - 0.2
            close = price + 1.8
            rd.update(high, low, close)
            price += 1.8
        # ADX should be positive after several trending bars
        assert rd.adx > 0.0

    def test_get_status_keys(self):
        rd = RegimeDetector(period=14)
        rd.update(100, 99, 100)
        s = rd.get_status()
        assert "regime" in s
        assert "adx" in s
        assert "trending_threshold" in s
        assert "ranging_threshold" in s
        assert "bars_seen" in s

    def test_flat_bars_do_not_crash(self):
        """Completely flat price → no DM, ADX stays near 0."""
        rd = RegimeDetector(period=14)
        for _ in range(30):
            rd.update(100.0, 100.0, 100.0)
        assert not math.isnan(rd.adx)
        assert rd.regime in (Regime.NEUTRAL, Regime.RANGING)


# ── D3 ATR-Adaptive Leverage tests ──────────────────────────────────────────


class TestAtrAdaptiveLeverage:
    def _rm(self, **kwargs) -> RiskManager:
        return RiskManager(event_bus=EventBus(), **kwargs)

    def test_no_adaptive_leverage_by_default(self):
        rm = self._rm(max_leverage=3)
        assert rm.effective_max_leverage == 3.0

    def test_high_atr_reduces_leverage(self):
        rm = self._rm(
            atr_adaptive_leverage=True,
            atr_window=20,
            atr_high_pct=80.0,
            atr_low_pct=20.0,
            atr_high_leverage=1.0,
            atr_low_leverage=3.0,
            max_leverage=3,
        )
        # Feed 20 bars with moderate ATR, then one very high ATR
        for _ in range(20):
            rm.update_market_data(101.0, 99.0, 100.0, atr=1.0)
        # High ATR bar (much larger than history)
        rm.update_market_data(105.0, 95.0, 100.0, atr=50.0)
        # Should now be in high-volatility regime → leverage drops to 1x
        assert rm.effective_max_leverage == 1.0

    def test_low_atr_allows_high_leverage(self):
        rm = self._rm(
            atr_adaptive_leverage=True,
            atr_window=20,
            atr_high_pct=80.0,
            atr_low_pct=20.0,
            atr_high_leverage=1.0,
            atr_low_leverage=3.0,
            max_leverage=2,
        )
        # Feed mostly high ATR bars, then one very low ATR
        for _ in range(20):
            rm.update_market_data(105.0, 95.0, 100.0, atr=10.0)
        rm.update_market_data(100.1, 99.9, 100.0, atr=0.01)
        assert rm.effective_max_leverage == 3.0

    def test_medium_atr_uses_default_leverage(self):
        rm = self._rm(
            atr_adaptive_leverage=True,
            atr_window=10,
            atr_high_pct=80.0,
            atr_low_pct=20.0,
            atr_high_leverage=1.0,
            atr_low_leverage=3.0,
            max_leverage=2,
        )
        # Feed bars with spread ATR values 1..10 so mid-value (5) is ~40th pct
        for atr_val in range(1, 11):
            rm.update_market_data(101.0, 99.0, 100.0, atr=float(atr_val))
        # ATR=5 is at ~40th percentile → between 20 and 80 → use default max_leverage
        rm.update_market_data(101.0, 99.0, 100.0, atr=5.0)
        assert rm.effective_max_leverage == 2.0

    def test_adaptive_leverage_disabled_ignores_atr(self):
        rm = self._rm(atr_adaptive_leverage=False, max_leverage=3)
        for _ in range(20):
            rm.update_market_data(105.0, 95.0, 100.0, atr=50.0)
        # Should remain unchanged since adaptive is off
        assert rm.effective_max_leverage == 3.0

    def test_atr_zero_skipped(self):
        """ATR=0 bars should not be added to history."""
        rm = self._rm(
            atr_adaptive_leverage=True,
            atr_window=10,
            max_leverage=2,
        )
        for _ in range(30):
            rm.update_market_data(101.0, 99.0, 100.0, atr=0.0)
        # Window should be empty → leverage unchanged
        assert rm.effective_max_leverage == 2.0


# ── D2 integration: regime blocking in RiskManager ──────────────────────────


class TestRegimeBlockInRiskManager:
    def _rm(self, **kwargs) -> RiskManager:
        return RiskManager(event_bus=EventBus(), **kwargs)

    def _feed_ranging(self, rm: RiskManager, n: int = 80) -> None:
        price = 100.0
        direction = 1
        for _ in range(n):
            high = price + 0.3
            low = price - 0.3
            close = price + 0.1 * direction
            rm.update_market_data(high, low, close, atr=0.6)
            direction *= -1

    def _make_signal(self, strategy: str = "trend_donchian"):
        from datetime import UTC, datetime

        from bot.core.constants import OrderSide
        from bot.core.events import SignalEvent

        return SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_name=strategy,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.01,
            price=50000.0,
            source="test",
        )

    def test_trend_signal_blocked_in_ranging_market(self):
        rm = self._rm(
            block_trend_in_ranging=True,
            regime_trending_threshold=25.0,
            regime_ranging_threshold=20.0,
        )
        self._feed_ranging(rm)
        assert rm.regime == Regime.RANGING

        sig = self._make_signal(strategy="trend_donchian_BTCUSDT")
        passed, reason = rm.check_signal(sig, equity=10_000)
        assert passed is False
        assert "ranging" in reason.lower()

    def test_grid_signal_not_blocked_by_regime_filter(self):
        rm = self._rm(block_trend_in_ranging=True)
        self._feed_ranging(rm)

        sig = self._make_signal(strategy="grid_BTCUSDT")
        passed, _ = rm.check_signal(sig, equity=10_000)
        assert passed is True

    def test_regime_block_disabled_allows_trend_signal(self):
        rm = self._rm(block_trend_in_ranging=False)
        self._feed_ranging(rm)

        sig = self._make_signal(strategy="trend_donchian_BTCUSDT")
        passed, _ = rm.check_signal(sig, equity=10_000)
        assert passed is True

    def test_regime_property_accessible(self):
        rm = self._rm()
        # Before any bars, regime should be NEUTRAL
        assert rm.regime == Regime.NEUTRAL


class TestAdaptiveLeverageOrderScaling:
    def _rm(self, **kwargs) -> RiskManager:
        return RiskManager(event_bus=EventBus(), **kwargs)

    def _make_signal(self, leverage: float = 2.0):
        from datetime import UTC, datetime

        from bot.core.constants import OrderSide
        from bot.core.events import SignalEvent

        return SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_name="bridge_trend_donchian_btc",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.02,
            price=50_000.0,
            metadata={"requested_leverage": leverage},
            source="test",
        )

    def test_signal_quantity_is_scaled_down_when_effective_leverage_drops(self):
        rm = self._rm(atr_adaptive_leverage=True, max_leverage=3)
        for _ in range(20):
            rm.update_market_data(101.0, 99.0, 100.0, atr=1.0)
        rm.update_market_data(120.0, 80.0, 100.0, atr=50.0)

        scaled = rm._effective_signal_quantity(self._make_signal(leverage=2.0))
        assert scaled == pytest.approx(0.01)

    def test_reduce_only_signal_is_not_scaled(self):
        from datetime import UTC, datetime

        from bot.core.constants import OrderSide
        from bot.core.events import SignalEvent

        rm = self._rm(atr_adaptive_leverage=True, max_leverage=3)
        for _ in range(20):
            rm.update_market_data(101.0, 99.0, 100.0, atr=1.0)
        rm.update_market_data(120.0, 80.0, 100.0, atr=50.0)

        signal = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_name="bridge_trend_donchian_btc",
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            quantity=0.02,
            price=50_000.0,
            reduce_only=True,
            metadata={"requested_leverage": 2.0},
            source="test",
        )
        assert rm._effective_signal_quantity(signal) == pytest.approx(0.02)

    def test_runtime_market_updates_can_drive_adaptive_leverage_without_explicit_atr(self):
        rm = self._rm(atr_adaptive_leverage=True, max_leverage=3, atr_window=20)
        for _ in range(20):
            rm.update_market_data(101.0, 99.0, 100.0)
        rm.update_market_data(120.0, 80.0, 100.0)
        assert rm.effective_max_leverage == 1.0
