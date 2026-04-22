"""Minimal end-to-end smoke runner for strategies_lab.

Feeds synthetic bars + injected external data (spot price, funding, dominance)
into each lab strategy and prints generated signals. Does NOT touch the main
project's backtest engine or config.

Usage:
    PYTHONPATH=src python strategies_lab/examples/run_lab_smoke.py

This is a visual sanity check — not a portfolio backtest. For the implemented
phase-1 V8 runner, use `python scripts/run_v8_backtest.py --profile v8b`.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure src/ and project root are importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from bot.core.events import MarketEvent  # noqa: E402

from strategies_lab.btc_dominance_rotation import BtcDominanceRotationStrategy  # noqa: E402
from strategies_lab.cross_exchange_funding import CrossExchangeFundingStrategy  # noqa: E402
from strategies_lab.liquidation_hunter import LiquidationHunterStrategy  # noqa: E402
from strategies_lab.perp_spot_basis_arb import PerpSpotBasisArbStrategy  # noqa: E402
from strategies_lab.stat_arb_pairs import StatArbPairsStrategy  # noqa: E402


def bar(symbol, ts, o, h, low, c, v=1000.0):
    return MarketEvent(
        timestamp=ts, symbol=symbol, timeframe="4h",
        open=o, high=h, low=low, close=c, volume=v,
    )


def banner(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def demo_perp_spot_basis():
    banner("Strategy #1 — Perp-Spot Basis Arbitrage")
    s = PerpSpotBasisArbStrategy({"entry_threshold_annual": 15.0})
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)

    # Scenario: perp trades rich vs spot, funding positive → enter short
    s.inject_spot_price("BTCUSDT", 100_000)
    s.inject_funding_rate("BTCUSDT", 0.0005)  # ~54.75% annual funding
    perp_bar = bar("BTCUSDT", t0, 101_000, 101_500, 100_800, 101_200)
    for sig in s.on_bar(perp_bar):
        print(f"  [SIG] {sig.side.value} qty={sig.quantity} -- {sig.reason}")


def demo_stat_arb():
    banner("Strategy #3 — Stat-Arb Pairs (Kalman)")
    s = StatArbPairsStrategy({"entry_z": 1.5, "spread_window": 30})
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    total_signals = 0
    for i in range(80):
        ts = t0 + timedelta(hours=i)
        # Stable regime first 40, then ETH dislocates
        eth = 3000 + math.sin(i / 5) * 20 + (200 if i >= 50 else 0)
        btc = 50_000 + math.sin(i / 5) * 300
        s.on_bar(bar("ETHUSDT", ts, eth, eth + 5, eth - 5, eth))
        sigs = s.on_bar(bar("BTCUSDT", ts, btc, btc + 30, btc - 30, btc))
        for sig in sigs:
            total_signals += 1
            print(f"  [SIG t={i}] {sig.side.value} -- {sig.reason}")
    print(f"  → Total signals generated: {total_signals}")


def demo_liquidation_hunter():
    banner("Strategy #4 — Liquidation Cascade Hunter")
    s = LiquidationHunterStrategy({
        "bar_range_pct_threshold": 0.03,
        "vol_lookback": 10,
        "consecutive_bars": 2,
    })
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # 10 calm bars
    for i in range(10):
        s.on_bar(bar("BTCUSDT", t0 + timedelta(minutes=i * 15), 100, 100.5, 99.5, 100, 1000))
    # 2 aligned down bars with vol spike
    s.on_bar(bar("BTCUSDT", t0 + timedelta(minutes=150), 100, 100.2, 96, 96.5, 5000))
    sigs = s.on_bar(bar("BTCUSDT", t0 + timedelta(minutes=165), 96, 96.2, 92, 92.5, 5000))
    for sig in sigs:
        print(f"  [SIG] {sig.side.value} qty={sig.quantity} -- {sig.reason}")


def demo_cross_exchange_funding():
    banner("Strategy #2 — Cross-Exchange Funding Delta")
    s = CrossExchangeFundingStrategy({"min_delta_annual": 10.0})
    s.set_equity(10_000)
    s.inject_funding("binance", "ETHUSDT", 0.0001)   # ~10.95% annual
    s.inject_funding("bybit", "ETHUSDT", 0.0008)     # ~87.6% annual
    s.inject_funding("okx", "ETHUSDT", 0.0003)       # ~32.85% annual
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    sigs = s.on_bar(bar("ETHUSDT", t0, 3000, 3010, 2990, 3005))
    for sig in sigs:
        print(f"  [SIG] {sig.side.value} qty={sig.quantity} -- {sig.reason}")
        print(f"        remote_leg: {sig.metadata.get('remote_leg_venue')} "
              f"side={sig.metadata.get('remote_leg_side')}")


def demo_btc_dominance():
    banner("Strategy #5 — BTC Dominance Rotation")
    s = BtcDominanceRotationStrategy({
        "btc_d_ma_period": 10,
        "alt_basket": ["ETHUSDT", "SOLUSDT"],
    })
    s.set_equity(10_000)
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # Baseline stable
    for _ in range(10):
        s.inject_dominance(60.0, 10.0)
    # Alt season transition
    for i in range(5):
        s.inject_dominance(55.0 - i * 0.3, 12.0 + i * 0.2)
    sigs = s.on_bar(bar("ETHUSDT", t0, 3000, 3010, 2990, 3005))
    sigs += s.on_bar(bar("SOLUSDT", t0, 150, 151, 149, 150.5))
    for sig in sigs:
        print(f"  [SIG] {sig.side.value} {sig.symbol} qty={sig.quantity} -- {sig.reason}")


def main():
    demo_perp_spot_basis()
    demo_cross_exchange_funding()
    demo_stat_arb()
    demo_liquidation_hunter()
    demo_btc_dominance()
    print("\n[OK] All 5 lab strategies executed without error.")


if __name__ == "__main__":
    main()
