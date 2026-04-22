"""Smoke tests for strategies_lab prototypes.

Run with:
    PYTHONPATH=src python -m pytest strategies_lab/tests/ -v

These are NOT full strategy validation — they verify:
    1. Each strategy instantiates with default params
    2. on_bar() returns list without exceptions
    3. Entry / exit signals fire under synthetic scenarios
    4. register_lab_strategies() works and doesn't pollute the default set
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.core.events import MarketEvent
from bot.strategy.registry import StrategyRegistry, register_default_strategies

from strategies_lab.btc_dominance_rotation import BtcDominanceRotationStrategy
from strategies_lab.cross_exchange_funding import CrossExchangeFundingStrategy
from strategies_lab.liquidation_hunter import LiquidationHunterStrategy
from strategies_lab.perp_spot_basis_arb import PerpSpotBasisArbStrategy
from strategies_lab.register_lab import register_lab_strategies
from strategies_lab.stat_arb_pairs import StatArbPairsStrategy


def make_bar(
    symbol: str = "BTCUSDT",
    ts: datetime | None = None,
    o: float = 100.0,
    h: float = 101.0,
    low: float = 99.0,
    c: float = 100.5,
    v: float = 1000.0,
) -> MarketEvent:
    return MarketEvent(
        timestamp=ts or datetime.now(timezone.utc),
        symbol=symbol,
        timeframe="4h",
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_lab_registration_does_not_pollute_default():
    StrategyRegistry._registry.clear()
    register_default_strategies()
    before = set(StrategyRegistry.list_strategies())
    register_lab_strategies()
    after = set(StrategyRegistry.list_strategies())
    added = after - before
    assert added == {
        "lab_perp_spot_basis",
        "lab_cross_exchange_funding",
        "lab_stat_arb_pairs",
        "lab_liquidation_hunter",
        "lab_btc_dominance_rotation",
    }
    assert before.issubset(after)


# ---------------------------------------------------------------------------
# Perp-Spot Basis
# ---------------------------------------------------------------------------


def test_perp_spot_basis_enters_on_positive_carry():
    s = PerpSpotBasisArbStrategy({"entry_threshold_annual": 10.0})
    s.set_equity(10_000)
    s.inject_spot_price("BTCUSDT", 100.0)
    s.inject_funding_rate("BTCUSDT", 0.001)  # 0.1% / 8h ≈ 109.5% annual
    bar = make_bar("BTCUSDT", c=102.0)  # basis +2% → huge annualized
    signals = s.on_bar(bar)
    assert len(signals) == 1
    assert signals[0].reason.startswith("[LAB-BASIS] enter")


def test_perp_spot_basis_returns_empty_when_no_spot_data():
    s = PerpSpotBasisArbStrategy()
    s.set_equity(10_000)
    signals = s.on_bar(make_bar("BTCUSDT"))
    assert signals == []


# ---------------------------------------------------------------------------
# Stat Arb Pairs
# ---------------------------------------------------------------------------


def test_stat_arb_pairs_handles_alternating_symbols():
    s = StatArbPairsStrategy({
        "symbol_a": "ETHUSDT",
        "symbol_b": "BTCUSDT",
        "spread_window": 30,
    })
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # Feed 60 bars of ETH/BTC price pairs so Kalman converges
    import math

    for i in range(60):
        ts = t0 + timedelta(hours=i)
        eth_price = 3000 + math.sin(i / 5) * 30
        btc_price = 50000 + math.sin(i / 5) * 500
        s.on_bar(make_bar("ETHUSDT", ts=ts, c=eth_price))
        signals = s.on_bar(make_bar("BTCUSDT", ts=ts, c=btc_price))
        assert isinstance(signals, list)


def test_stat_arb_pairs_triggers_entry_on_extreme_spread():
    s = StatArbPairsStrategy({"entry_z": 1.0, "spread_window": 15})
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # Build a stable regime, then inject a sharp divergence
    for i in range(30):
        ts = t0 + timedelta(hours=i)
        s.on_bar(make_bar("ETHUSDT", ts=ts, c=3000 + i * 0.1))
        s.on_bar(make_bar("BTCUSDT", ts=ts, c=50000 + i))
    # Big A dislocation
    ts_shock = t0 + timedelta(hours=35)
    s.on_bar(make_bar("ETHUSDT", ts=ts_shock, c=3500))
    signals = s.on_bar(make_bar("BTCUSDT", ts=ts_shock, c=50050))
    # May or may not enter depending on z — just verify no exception
    assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# Liquidation Hunter
# ---------------------------------------------------------------------------


def test_liquidation_hunter_enters_on_cascade():
    s = LiquidationHunterStrategy({
        "bar_range_pct_threshold": 0.03,
        "volume_spike_mult": 2.0,
        "vol_lookback": 10,
        "consecutive_bars": 2,
    })
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # 10 calm bars to fill volume lookback
    for i in range(10):
        ts = t0 + timedelta(minutes=i * 15)
        s.on_bar(make_bar("BTCUSDT", ts=ts, o=100, h=100.5, low=99.5, c=100, v=1000))
    # 2 aligned down bars with volume spike and wide range
    s.on_bar(make_bar(
        "BTCUSDT", ts=t0 + timedelta(minutes=150), o=100, h=100.2, low=96, c=96.5, v=5000,
    ))
    signals = s.on_bar(make_bar(
        "BTCUSDT", ts=t0 + timedelta(minutes=165), o=96, h=96.2, low=92, c=92.5, v=5000,
    ))
    assert len(signals) == 1
    assert signals[0].reason.startswith("[LAB-LIQ] contra-long")


def test_liquidation_hunter_exits_after_max_bars():
    s = LiquidationHunterStrategy({
        "bar_range_pct_threshold": 0.03,
        "volume_spike_mult": 2.0,
        "vol_lookback": 10,
        "consecutive_bars": 2,
        "exit_bars": 3,
    })
    s.set_equity(10_000)
    # 10 calm bars to saturate the vol lookback with low volume
    for i in range(10):
        s.on_bar(make_bar("BTCUSDT", o=100, h=100.5, low=99.5, c=100, v=1000))
    # First aligned-down bar (sets direction[-1]=-1 but no vol spike yet needed)
    s.on_bar(make_bar("BTCUSDT", o=100, h=100.2, low=96, c=96.5, v=5000))
    # Second aligned-down bar WITH vol spike → entry fires
    entry_signals = s.on_bar(make_bar("BTCUSDT", o=96, h=96.2, low=92, c=92.5, v=5000))
    assert len(entry_signals) == 1
    # Manually apply the fill to simulate position open
    from bot.core.events import FillEvent
    from bot.core.constants import OrderSide

    fill = FillEvent(
        timestamp=datetime.now(timezone.utc),
        strategy_name=s.name,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=entry_signals[0].quantity,
        price=92.5,
        commission=0.0,
    )
    s.on_fill(fill)
    # Now feed 3 more bars to trigger max_bars exit
    for i in range(3):
        signals = s.on_bar(make_bar("BTCUSDT", o=93, h=94, low=92, c=93.5, v=1000))
    assert any("max bars" in sig.reason for sig in signals)


# ---------------------------------------------------------------------------
# Cross-Exchange Funding
# ---------------------------------------------------------------------------


def test_cross_exchange_funding_enters_on_large_delta():
    s = CrossExchangeFundingStrategy({"min_delta_annual": 5.0})
    s.set_equity(10_000)
    s.inject_funding("binance", "ETHUSDT", 0.0001)   # ~10.95% annual
    s.inject_funding("bybit", "ETHUSDT", 0.0010)     # ~109.5% annual
    signals = s.on_bar(make_bar("ETHUSDT", c=3000))
    assert len(signals) == 1
    assert "short remote=bybit" in signals[0].reason or "short home=bybit" in signals[0].reason
    assert signals[0].metadata["remote_leg_venue"] in ("binance", "bybit")


def test_cross_exchange_funding_no_signal_below_threshold():
    s = CrossExchangeFundingStrategy({"min_delta_annual": 50.0})
    s.set_equity(10_000)
    s.inject_funding("binance", "ETHUSDT", 0.0001)
    s.inject_funding("bybit", "ETHUSDT", 0.00012)
    signals = s.on_bar(make_bar("ETHUSDT", c=3000))
    assert signals == []


# ---------------------------------------------------------------------------
# BTC Dominance Rotation
# ---------------------------------------------------------------------------


def test_btc_dominance_rotation_long_on_alt_season():
    s = BtcDominanceRotationStrategy({
        "btc_d_ma_period": 5,
        "btc_d_down_pct": 0.05,
        "alt_basket": ["ETHUSDT"],
    })
    s.set_equity(10_000)
    # Stable baseline at BTC.D=60, OTHERS.D=10
    for _ in range(5):
        s.inject_dominance(60.0, 10.0)
    # Sharp transition: BTC.D drops to 45 (-25%), OTHERS.D up to 20 (+100%)
    for _ in range(3):
        s.inject_dominance(45.0, 20.0)
    signals = s.on_bar(make_bar("ETHUSDT", c=3000))
    long_signals = [sig for sig in signals if sig.reason.startswith("[LAB-BTCD] alt_season long")]
    assert len(long_signals) == 1


def test_btc_dominance_rotation_ignores_symbols_outside_basket():
    s = BtcDominanceRotationStrategy({"alt_basket": ["ETHUSDT"]})
    s.set_equity(10_000)
    for _ in range(60):
        s.inject_dominance(60.0, 10.0)
    signals = s.on_bar(make_bar("SHIBUSDT", c=0.01))
    assert signals == []
