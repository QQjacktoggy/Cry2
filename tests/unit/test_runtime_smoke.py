"""Tests for local runtime smoke validation."""

from __future__ import annotations

import json

from bot.monitoring.runtime_smoke import build_runtime_smoke_report
from bot.strategy.bridge import create_v74_strategies


def test_build_runtime_smoke_report_writes_dashboard_compatible_health_snapshot(tmp_path) -> None:
    health_path = tmp_path / "health.json"

    report = build_runtime_smoke_report(
        strategies=create_v74_strategies(initial_capital=150.0),
        capital=150.0,
        version="v74",
        health_path=health_path,
    )

    assert report["all_checks_passed"] is True
    assert report["strategy_count"] == 16
    assert report["symbol_count"] == 5
    assert report["allocation_pct"] == 100.0
    assert health_path.exists()

    state = json.loads(health_path.read_text(encoding="utf-8"))
    assert state["ws_connected"] is False
    assert state["user_data_stream_ok"] is False
    assert state["circuit_breaker_symbol"] == ""
    assert state["risk_manager"]["is_halted"] is False
    assert state["regime"]["effective_leverage"] == 3.0
    assert state["smoke_validation"]["version"] == "v74"
    assert state["smoke_validation"]["strategy_count"] == 16
    assert state["smoke_validation"]["symbol_count"] == 5
