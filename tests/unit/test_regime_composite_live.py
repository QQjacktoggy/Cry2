"""Unit tests for RegimeCompositeLiveStrategy."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from bot.core.constants import OrderSide, OrderType
from bot.core.events import MarketEvent, SignalEvent
from bot.risk.regime_detector import Regime
from bot.strategy.regime_composite_config import RegimeCompositeConfig
from bot.strategy.regime_composite_live import RegimeCompositeLiveStrategy


def _make_market_event(symbol: str = "BTCUSDT", close: float = 50000.0) -> MarketEvent:
    return MarketEvent(
        timestamp=datetime.now(UTC),
        symbol=symbol,
        timeframe="15m",
        open=close - 10,
        high=close + 50,
        low=close - 50,
        close=close,
        volume=100.0,
        source="test",
    )


def _make_signal(symbol: str = "BTCUSDT", quantity: float = 0.01, lev: float = 7.0) -> SignalEvent:
    return SignalEvent(
        timestamp=datetime.now(UTC),
        strategy_name="rc_trend_btc",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=50000.0,
        metadata={"requested_leverage": lev},
        source="test",
    )


def _make_strategy(
    conservative_mode: bool = False,
    regime: Regime = Regime.TRENDING,
) -> RegimeCompositeLiveStrategy:
    rm = MagicMock()
    rm.conservative_mode = conservative_mode
    type(rm).regime = PropertyMock(return_value=regime)
    rm.update_market_data = MagicMock()

    cfg = RegimeCompositeConfig(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="15m",
        daily_profit_target_usd=20.0,
        conservative_size_factor=0.25,
        conservative_leverage=1,
        aggressive_leverage_trending=7,
        aggressive_leverage_ranging=3,
        aggressive_leverage_neutral=2,
        skip_entries_in_volatile=True,
        max_sl_pct=1.5,
        allocation_usd=110.0,
        warmup=5,  # short warmup for tests
    )
    return RegimeCompositeLiveStrategy(risk_manager=rm, config=cfg)


class TestRegimeCompositeLive:
    def test_strategy_name_is_regime_composite(self):
        s = _make_strategy()
        assert s.name == "regime_composite"

    def test_symbols_property(self):
        s = _make_strategy()
        assert "BTCUSDT" in s.symbols
        assert "ETHUSDT" in s.symbols

    def test_symbol_property_returns_first(self):
        s = _make_strategy()
        assert s.symbol == "BTCUSDT"

    def test_ignored_symbol_returns_empty(self):
        s = _make_strategy()
        event = _make_market_event(symbol="SOLUSDT")
        signals = s.on_bar(event)
        assert signals == []

    def test_apply_mode_override_conservative_scales_down(self):
        s = _make_strategy(conservative_mode=True, regime=Regime.TRENDING)
        signal = _make_signal(quantity=0.1, lev=7.0)
        result = s._apply_mode_override(signal, Regime.TRENDING, conservative=True)
        assert result is not None
        assert result.quantity == pytest.approx(0.1 * 0.25)
        assert result.metadata["requested_leverage"] == 1

    def test_apply_mode_override_aggressive_trending_preserves_leverage(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.TRENDING)
        signal = _make_signal(quantity=0.1, lev=7.0)
        result = s._apply_mode_override(signal, Regime.TRENDING, conservative=False)
        assert result is not None
        assert result.quantity == pytest.approx(0.1)
        assert result.metadata["requested_leverage"] == 7

    def test_apply_mode_override_ranging_sets_3x(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.RANGING)
        signal = _make_signal(quantity=0.1, lev=3.0)
        result = s._apply_mode_override(signal, Regime.RANGING, conservative=False)
        assert result is not None
        assert result.metadata["requested_leverage"] == 3

    def test_neutral_entry_skipped_when_skip_enabled(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.NEUTRAL)
        signal = _make_signal(quantity=0.1)
        result = s._apply_mode_override(signal, Regime.NEUTRAL, conservative=False)
        assert result is None

    def test_neutral_exit_not_skipped(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.NEUTRAL)
        signal = _make_signal(quantity=0.1)
        # Make it a reduce-only (exit) signal
        exit_signal = signal.model_copy(update={"reduce_only": True})
        result = s._apply_mode_override(exit_signal, Regime.NEUTRAL, conservative=False)
        assert result is not None

    def test_metadata_includes_regime_and_sl(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.TRENDING)
        signal = _make_signal(quantity=0.1, lev=7.0)
        result = s._apply_mode_override(signal, Regime.TRENDING, conservative=False)
        assert "max_sl_pct" in result.metadata
        assert result.metadata["max_sl_pct"] == 1.5
        assert "regime" in result.metadata

    def test_select_bridge_trending_returns_trend_bridge(self):
        s = _make_strategy()
        bridge = s._select_bridge("BTCUSDT", Regime.TRENDING)
        assert bridge is s._trend_bridges["BTCUSDT"]

    def test_select_bridge_ranging_returns_mr_bridge(self):
        s = _make_strategy()
        bridge = s._select_bridge("BTCUSDT", Regime.RANGING)
        assert bridge is s._mr_bridges["BTCUSDT"]

    def test_select_bridge_neutral_returns_mr_bridge(self):
        s = _make_strategy()
        bridge = s._select_bridge("BTCUSDT", Regime.NEUTRAL)
        assert bridge is s._mr_bridges["BTCUSDT"]

    def test_on_bar_delegates_to_bridge_and_overrides(self):
        s = _make_strategy(conservative_mode=False, regime=Regime.TRENDING)

        # Inject a fake signal from the trend bridge
        fake_signal = _make_signal(quantity=0.1, lev=7.0)
        s._trend_bridges["BTCUSDT"].on_bar = MagicMock(return_value=[fake_signal])

        event = _make_market_event("BTCUSDT")
        signals = s.on_bar(event)

        # Strategy should have processed and returned the overridden signal
        assert len(signals) == 1
        assert signals[0].strategy_name == "regime_composite"

    def test_conservative_mode_reduces_quantity_in_on_bar(self):
        s = _make_strategy(conservative_mode=True, regime=Regime.TRENDING)
        fake_signal = _make_signal(quantity=0.1, lev=7.0)
        s._trend_bridges["BTCUSDT"].on_bar = MagicMock(return_value=[fake_signal])

        event = _make_market_event("BTCUSDT")
        signals = s.on_bar(event)

        assert len(signals) == 1
        assert signals[0].quantity == pytest.approx(0.1 * 0.25)
        assert signals[0].metadata["requested_leverage"] == 1
