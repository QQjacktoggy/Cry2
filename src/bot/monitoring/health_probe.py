"""Machine-readable health probe.

Operators (systemd, GCP uptime checks, ops scripts) call this as:

    python -m bot.monitoring.health_probe --path /data/bot/health.json

Exit codes
----------
0  — all checks passed (bot is healthy)
1  — health file missing / stale / one or more checks failed

Checks
------
* Health file exists and is fresh (``max_age_sec``, default 120s).
* Market WebSocket is connected (``ws_connected``).
* User data stream is healthy (``user_data_stream_ok``).
* Kill switch not triggered.
* Circuit breaker not tripped.
* Risk manager not halted.

The probe never talks to Binance directly — it only inspects the JSON
that the running bot writes via :class:`bot.monitoring.health_state.HealthStateWriter`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class ProbeResult:
    """Structured outcome of a health probe invocation."""

    healthy: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "checks": list(self.checks),
            "error": self.error,
        }


def probe_health(
    path: str | Path,
    *,
    max_age_sec: float = 120.0,
    user_data_grace_sec: float = 180.0,
) -> ProbeResult:
    """Read the health file at ``path`` and evaluate freshness + flags."""
    p = Path(path)
    if not p.exists():
        return ProbeResult(healthy=False, error=f"health file not found: {p}")

    try:
        stat = p.stat()
        age_sec = max(time.time() - stat.st_mtime, 0.0)
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return ProbeResult(healthy=False, error=f"cannot read health file: {e}")

    checks: list[dict[str, Any]] = []

    def _record(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    _record(
        "freshness",
        passed=age_sec <= max_age_sec,
        detail=f"age_sec={age_sec:.1f} max={max_age_sec:.0f}",
    )
    _record("ws_connected", bool(data.get("ws_connected", False)))
    uds_ok = bool(data.get("user_data_stream_ok", False))
    uds_reconnecting = bool(data.get("user_data_stream_reconnecting", False))
    grace_until_raw = str(data.get("user_data_stream_grace_until", "") or "").strip()
    uds_detail = ""
    uds_passed = uds_ok
    if not uds_ok and uds_reconnecting and grace_until_raw:
        try:
            grace_until = datetime.fromisoformat(grace_until_raw)
        except ValueError:
            grace_until = None
        if grace_until is not None:
            remaining = (grace_until - datetime.now(UTC)).total_seconds()
            uds_passed = remaining >= 0
            uds_detail = f"reconnecting grace_remaining_sec={max(remaining, 0.0):.1f}"
        else:
            uds_detail = "invalid grace timestamp"
    elif not uds_ok and uds_reconnecting:
        uds_detail = f"reconnecting grace_window_sec={user_data_grace_sec:.0f}"
    _record("user_data_stream_ok", uds_passed, uds_detail)
    _record("kill_switch_clear", not bool(data.get("kill_switch_triggered", False)))
    _record("circuit_breaker_clear", not bool(data.get("circuit_breaker_tripped", False)))

    risk = data.get("risk_manager") or {}
    _record(
        "risk_manager_not_halted",
        passed=not bool(risk.get("is_halted", False)),
        detail=(
            f"daily={risk.get('daily_halted')} weekly={risk.get('weekly_halted')} "
            f"drawdown={risk.get('drawdown_halted')}"
        ),
    )

    healthy = all(check["passed"] for check in checks)
    return ProbeResult(healthy=healthy, checks=checks)


def resolve_health_path(
    path: str | None,
    *,
    config_path: str | Path = "config/config.yaml",
) -> Path:
    """Resolve the probe target path, defaulting to config.paths.data_dir."""
    if path:
        return Path(path)

    try:
        from bot.config.loader import load_config

        config = load_config(config_path)
        data_dir = Path(config.get("paths", {}).get("data_dir", "./data"))
        return data_dir / "health.json"
    except Exception:
        return Path("./data/health.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bot health probe")
    parser.add_argument(
        "--path",
        default=None,
        help="Path to health snapshot JSON file",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Config file used to resolve the default health path",
    )
    parser.add_argument(
        "--max-age-sec",
        type=float,
        default=120.0,
        help="Maximum allowed age of the health file in seconds",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text",
    )
    args = parser.parse_args(argv)

    health_path = resolve_health_path(args.path, config_path=args.config)
    result = probe_health(health_path, max_age_sec=args.max_age_sec)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        banner = "🟢 HEALTHY" if result.healthy else "🔴 UNHEALTHY"
        print(banner)
        if result.error:
            print(f"error: {result.error}")
        for check in result.checks:
            mark = "✅" if check["passed"] else "❌"
            detail = f" — {check['detail']}" if check["detail"] else ""
            print(f"{mark} {check['name']}{detail}")

    return 0 if result.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
