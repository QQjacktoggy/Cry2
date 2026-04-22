"""Tests for bot.monitoring.health_probe."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.monitoring.health_probe import probe_health, resolve_health_path


def _write_health(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_healthy_snapshot(tmp_path: Path) -> None:
    p = tmp_path / "health.json"
    _write_health(
        p,
        {
            "ws_connected": True,
            "user_data_stream_ok": True,
            "kill_switch_triggered": False,
            "circuit_breaker_tripped": False,
            "risk_manager": {"is_halted": False},
        },
    )
    result = probe_health(p, max_age_sec=60.0)
    assert result.healthy, result.checks


def test_missing_file_unhealthy(tmp_path: Path) -> None:
    result = probe_health(tmp_path / "nope.json")
    assert not result.healthy
    assert "not found" in result.error


def test_stale_file_unhealthy(tmp_path: Path) -> None:
    p = tmp_path / "health.json"
    _write_health(
        p,
        {
            "ws_connected": True,
            "user_data_stream_ok": True,
            "kill_switch_triggered": False,
            "circuit_breaker_tripped": False,
            "risk_manager": {"is_halted": False},
        },
    )
    old = time.time() - 10_000
    os.utime(p, (old, old))
    result = probe_health(p, max_age_sec=60.0)
    freshness = next(c for c in result.checks if c["name"] == "freshness")
    assert not freshness["passed"]
    assert not result.healthy


def test_halted_risk_manager_unhealthy(tmp_path: Path) -> None:
    p = tmp_path / "health.json"
    _write_health(
        p,
        {
            "ws_connected": True,
            "user_data_stream_ok": True,
            "kill_switch_triggered": False,
            "circuit_breaker_tripped": False,
            "risk_manager": {"is_halted": True, "drawdown_halted": True},
        },
    )
    result = probe_health(p)
    assert not result.healthy
    risk_check = next(c for c in result.checks if c["name"] == "risk_manager_not_halted")
    assert not risk_check["passed"]


def test_user_data_reconnecting_within_grace_is_healthy(tmp_path: Path) -> None:
    p = tmp_path / "health.json"
    _write_health(
        p,
        {
            "ws_connected": True,
            "user_data_stream_ok": False,
            "user_data_stream_reconnecting": True,
            "user_data_stream_grace_until": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            "kill_switch_triggered": False,
            "circuit_breaker_tripped": False,
            "risk_manager": {"is_halted": False},
        },
    )
    result = probe_health(p, max_age_sec=60.0)
    assert result.healthy, result.checks


def test_resolve_health_path_uses_config_data_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths:\n  data_dir: ./custom-data\n", encoding="utf-8")
    resolved = resolve_health_path(None, config_path=config_path)
    assert resolved == Path("custom-data") / "health.json"
