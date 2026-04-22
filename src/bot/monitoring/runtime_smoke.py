"""Helpers for local runtime smoke validation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from bot.core.event_bus import EventBus
from bot.monitoring.health_state import HealthStateWriter
from bot.risk.risk_manager import RiskManager

_PORTFOLIO_EXPECTATIONS: dict[str, dict[str, int]] = {
    "v72": {"strategy_count": 16, "symbol_count": 5},
    "v74": {"strategy_count": 16, "symbol_count": 5},
}


class RuntimeSmokeStrategy(Protocol):
    """Structural type for bridged strategies used in smoke validation."""

    name: str
    symbol: str
    timeframe: str
    leverage: float
    _allocation_usd: float

    def on_bar(self, event: Any) -> list[Any]:
        ...


def write_runtime_health_snapshot(
    *,
    version: str,
    strategies: Sequence[RuntimeSmokeStrategy],
    health_path: str | Path = "./data/health.json",
) -> dict[str, Any]:
    """Write a baseline health snapshot that matches dashboard expectations."""
    event_bus = EventBus()
    risk_manager = RiskManager(event_bus=event_bus)
    symbols = sorted({strategy.symbol for strategy in strategies})
    timeframes = sorted({strategy.timeframe for strategy in strategies})

    health = HealthStateWriter(health_path)
    health.update(
        ws_connected=False,
        user_data_stream_ok=False,
        last_bar_ts=None,
        last_reconcile_ts=None,
        kill_switch_triggered=False,
        circuit_breaker_tripped=False,
        circuit_breaker_symbol="",
        api_latency_ms=None,
        risk_manager=risk_manager.get_status(),
        regime=risk_manager.get_regime_status(),
        smoke_validation={
            "version": version,
            "generated_at": datetime.now(UTC).isoformat(),
            "strategy_count": len(strategies),
            "symbol_count": len(symbols),
            "timeframes": timeframes,
        },
    )
    return health.snapshot()


def build_runtime_smoke_report(
    *,
    strategies: Sequence[RuntimeSmokeStrategy],
    capital: float,
    version: str,
    health_path: str | Path = "./data/health.json",
) -> dict[str, Any]:
    """Collect strategy summary, write health snapshot, and evaluate smoke checks."""
    symbols = sorted({strategy.symbol for strategy in strategies})
    timeframes = sorted({strategy.timeframe for strategy in strategies})
    strategy_names = [strategy.name for strategy in strategies]
    total_alloc = sum(strategy._allocation_usd for strategy in strategies)
    alloc_pct = (total_alloc / capital * 100) if capital else 0.0
    expected = _PORTFOLIO_EXPECTATIONS.get(version, {})
    health_file = Path(health_path)
    snapshot = write_runtime_health_snapshot(
        version=version,
        strategies=strategies,
        health_path=health_file,
    )

    leverage_by_symbol: dict[str, float] = {}
    for strategy in strategies:
        leverage_by_symbol[strategy.symbol] = max(
            leverage_by_symbol.get(strategy.symbol, 0.0),
            float(strategy.leverage),
        )

    checks = {
        "allocation_sum": 99 <= alloc_pct <= 101,
        "strategy_count": (
            len(strategies) == expected["strategy_count"]
            if "strategy_count" in expected
            else len(strategies) > 0
        ),
        "coin_count": (
            len(symbols) == expected["symbol_count"]
            if "symbol_count" in expected
            else len(symbols) > 0
        ),
        "all_strategies_have_on_bar": all(callable(getattr(s, "on_bar", None)) for s in strategies),
        "strategy_names_unique": len(strategy_names) == len(set(strategy_names)),
        "health_snapshot_written": health_file.exists(),
    }

    return {
        "version": version,
        "capital": capital,
        "strategy_count": len(strategies),
        "symbol_count": len(symbols),
        "symbols": symbols,
        "timeframes": timeframes,
        "total_alloc": total_alloc,
        "allocation_pct": alloc_pct,
        "strategies": [
            {
                "name": strategy.name,
                "symbol": strategy.symbol,
                "timeframe": strategy.timeframe,
                "allocation_usd": strategy._allocation_usd,
                "leverage": float(strategy.leverage),
            }
            for strategy in strategies
        ],
        "leverage_by_symbol": leverage_by_symbol,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "health_path": str(health_file),
        "health_snapshot": snapshot,
    }


def print_runtime_smoke_report(report: dict[str, Any], *, runtime_label: str) -> None:
    """Render the smoke validation summary as CLI output."""
    print("=" * 60)
    print(f"🧪 DRY RUN — {runtime_label} Trading Validation")
    print("=" * 60)

    print(f"\n📊 Portfolio Version: {str(report['version']).upper()}")
    print(f"💰 Capital: ${float(report['capital']):.0f} USDT")
    print(f"📈 Strategies: {report['strategy_count']}")
    print(f"🪙 Coins: {', '.join(sym.replace('USDT', '') for sym in report['symbols'])}")
    print(f"⏰ Timeframes: {', '.join(report['timeframes'])}")
    print(
        f"💵 Total Allocated: ${float(report['total_alloc']):.1f} "
        f"({float(report['allocation_pct']):.0f}%)"
    )
    print(f"🩺 Health Snapshot: {report['health_path']}")

    print(f"\n{'─' * 60}")
    print(f"{'Strategy':<35} {'Symbol':<10} {'TF':<5} {'Alloc':>8} {'Lev':>4}")
    print(f"{'─' * 60}")
    for strategy in report["strategies"]:
        print(
            f"{strategy['name']:<35} {strategy['symbol']:<10} {strategy['timeframe']:<5} "
            f"${float(strategy['allocation_usd']):>6.1f} {float(strategy['leverage']):>3.1f}x"
        )
    print(f"{'─' * 60}")
    print(f"{'TOTAL':<35} {'':10} {'':5} ${float(report['total_alloc']):>6.1f}")

    print(f"\n{'=' * 60}")
    print("✅ Checks:")
    for key, passed in report["checks"].items():
        label = key.replace("_", " ")
        print(f"  {'✅' if passed else '❌'} {label}")

    print(f"\n{'🟢 ALL CHECKS PASSED' if report['all_checks_passed'] else '🔴 SOME CHECKS FAILED'}")
    print(f"{'=' * 60}")
