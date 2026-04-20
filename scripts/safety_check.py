#!/usr/bin/env python3
"""Pre-deployment safety validation for live trading.

Checks that all critical components are properly configured
before allowing the system to trade with real money.

Usage:
    python scripts/safety_check.py
    python scripts/safety_check.py --config config/config.yaml
"""

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅" if condition else "❌"
    msg = f"  {status} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main() -> None:
    parser = argparse.ArgumentParser(description="Safety check")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    print("=" * 60)
    print("🔒 Cry2 Pre-Deployment Safety Check")
    print("=" * 60)
    passed = 0
    failed = 0
    total = 0

    # ── 1. Module imports ──────────────────────────────────────
    print("\n📦 Module Imports:")
    modules = [
        ("bot.core.event_bus", "EventBus"),
        ("bot.core.events", "MarketEvent, FundingEvent, SignalEvent"),
        ("bot.exchange.binance_rest", "BinanceRestClient"),
        ("bot.execution.executor_live", "LiveExecutor"),
        ("bot.strategy.bridge", "VBTBridgeStrategy"),
        ("bot.data.feed_live", "LiveFeed"),
        ("bot.risk.risk_manager", "RiskManager"),
        ("bot.risk.circuit_breaker", "CircuitBreaker"),
        ("bot.risk.kill_switch", "KillSwitch"),
        ("bot.portfolio.portfolio", "Portfolio"),
    ]
    for mod_path, desc in modules:
        total += 1
        try:
            importlib.import_module(mod_path)
            ok = check(f"import {mod_path}", True, desc)
        except Exception as e:
            ok = check(f"import {mod_path}", False, str(e))
        passed += ok
        failed += not ok

    # ── 2. Strategy bridge ─────────────────────────────────────
    print("\n🎯 V6 Strategy Bridge:")
    total += 1
    try:
        from bot.strategy.bridge import create_v6_strategies
        strategies = create_v6_strategies(initial_capital=150)
        ok = check("create_v6_strategies()", len(strategies) == 12,
                    f"{len(strategies)} strategies loaded")
        passed += ok
        failed += not ok
    except Exception as e:
        check("create_v6_strategies()", False, str(e))
        failed += 1
        strategies = []

    if strategies:
        symbols = sorted(set(s.symbol for s in strategies))
        total += 1
        ok = check("5-coin coverage", len(symbols) >= 5, f"coins: {symbols}")
        passed += ok
        failed += not ok

        total += 1
        names = [s.name for s in strategies]
        ok = check("strategy names unique", len(names) == len(set(names)),
                    f"{len(names)} names")
        passed += ok
        failed += not ok

    # ── 3. Config files ────────────────────────────────────────
    print("\n📋 Config Files:")
    config_files = [
        "config/config.yaml",
        "config/strategies.yaml",
        "config/optimized_params.yaml",
    ]
    for cf in config_files:
        total += 1
        ok = check(cf, Path(cf).exists())
        passed += ok
        failed += not ok

    # ── 4. Data files ──────────────────────────────────────────
    print("\n📊 Backtest Data:")
    data_dir = Path("backtest_tool/data/klines")
    required_coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]
    for coin in required_coins:
        total += 1
        coin_dir = data_dir / coin
        has_data = coin_dir.exists() and any(coin_dir.rglob("*.parquet"))
        ok = check(f"{coin} data", has_data,
                    f"{sum(1 for _ in coin_dir.rglob('*.parquet'))} files" if has_data else "missing")
        passed += ok
        failed += not ok

    # ── 5. LiveExecutor order tracking ─────────────────────────
    print("\n🔧 LiveExecutor Fixes:")
    total += 1
    try:
        from bot.execution.executor_live import LiveExecutor
        import inspect
        src = inspect.getsource(LiveExecutor.__init__)
        ok = check("order→symbol tracking", "_order_symbols" in src,
                    "cancel_order() fix verified")
        passed += ok
        failed += not ok
    except Exception as e:
        check("order→symbol tracking", False, str(e))
        failed += 1

    # ── 6. Funding rate API ────────────────────────────────────
    print("\n💰 Funding Rate Feed:")
    total += 1
    try:
        from bot.exchange.binance_rest import BinanceRestClient
        ok = check("get_funding_rate()", hasattr(BinanceRestClient, "get_funding_rate"))
        passed += ok
        failed += not ok
    except Exception as e:
        check("get_funding_rate()", False, str(e))
        failed += 1

    total += 1
    try:
        from bot.data.feed_live import LiveFeed
        import inspect
        src = inspect.getsource(LiveFeed)
        ok = check("funding polling in LiveFeed", "_poll_funding_rates" in src)
        passed += ok
        failed += not ok
    except Exception as e:
        check("funding polling in LiveFeed", False, str(e))
        failed += 1

    # ── 7. Risk management ─────────────────────────────────────
    print("\n🛡️ Risk Controls:")
    risk_modules = [
        "bot.risk.circuit_breaker",
        "bot.risk.kill_switch",
        "bot.risk.risk_manager",
        "bot.risk.position_sizer",
    ]
    for rm in risk_modules:
        total += 1
        try:
            importlib.import_module(rm)
            ok = check(rm, True)
        except Exception:
            ok = check(rm, False, "not found")
        passed += ok
        failed += not ok

    # ── 8. run_live.py completeness ────────────────────────────
    print("\n🚀 run_live.py:")
    total += 1
    run_live = Path("scripts/run_live.py").read_text(encoding="utf-8")
    ok = check("not a stub", "TODO" not in run_live and "create_v6_strategies" in run_live)
    passed += ok
    failed += not ok

    total += 1
    ok = check("confirmation required", "CONFIRM_LIVE_TRADING" in run_live)
    passed += ok
    failed += not ok

    total += 1
    ok = check("signal handling", "SIGINT" in run_live and "SIGTERM" in run_live)
    passed += ok
    failed += not ok

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    pct = (passed / total * 100) if total else 0
    emoji = "🟢" if failed == 0 else "🟡" if failed <= 2 else "🔴"
    print(f"{emoji} Safety Check: {passed}/{total} passed ({pct:.0f}%)")
    if failed > 0:
        print(f"   ⚠️  {failed} check(s) failed — fix before deploying")
    else:
        print("   ✅ All checks passed — ready for paper trading")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
