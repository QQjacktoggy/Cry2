"""Preflight checks: run once at startup before we touch the exchange.

Philosophy
----------
We want a single, deterministic function that either returns a clean
report (every check PASS) or exits non-zero so systemd / CI can halt
the boot. The reason is that silent soft-failures (``logger.warning``
and proceed) are exactly how production incidents leak onto a testnet
account at 3 AM.

Covered checks
--------------
- Required secrets present (names passed by caller)
- Paths writable (journal DB directory, health file directory, logs)
- Exchange reachable (skipped when ``client`` is ``None`` — e.g. dry-run)
- Environment consistency (config env matches runtime flag)

Not covered here (on purpose)
-----------------------------
- Single-instance lock — owned by ``SingleInstance`` so the order is
  "acquire lock, then preflight"; otherwise a failed preflight from
  process B could be mistaken for a real outage on process A.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ExchangeProbe(Protocol):
    """Minimal surface we need to confirm the REST client is alive."""

    def ping(self) -> float:
        ...


@dataclass
class PreflightReport:
    """Structured outcome of preflight."""

    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(check["passed"] for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})

    def as_dict(self) -> dict[str, Any]:
        return {"all_passed": self.all_passed, "checks": list(self.checks)}


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def run_preflight(
    *,
    required_secrets: list[str],
    writable_paths: list[str | Path],
    exchange_client: ExchangeProbe | None = None,
    expected_environment: str,
    config_environment: str,
    secret_resolver: Callable[[str], str] | None = None,
) -> PreflightReport:
    """Run all preflight checks and return a structured report.

    Args:
        required_secrets: Environment variable names that must be non-empty.
            (We intentionally check env vars, not Secret Manager, because
            when Secret Manager is wired in the secret values are pushed
            into env by the loader before preflight runs.)
        writable_paths: Directories we need to write into (journal DB,
            health file, logs, runs dir). Files inside are probed.
        exchange_client: Object with a ``ping()`` method returning latency
            in ms. Pass ``None`` to skip connectivity check.
        expected_environment: The environment the runner *thinks* it is
            in (``"paper"`` / ``"live"``).
        config_environment: The environment the merged config resolved
            to. These must match — a mismatch usually means the wrong
            config bundle was deployed.
        secret_resolver: Optional callback used when a required secret is
            absent from ``os.environ``. Runners can pass
            ``lambda name: get_secret(name, required=True)`` to let
            preflight validate Secret Manager-backed deployments too.
    """
    report = PreflightReport()

    # 1. Required secrets
    for name in required_secrets:
        value = os.environ.get(name, "")
        missing_detail = "missing or empty"
        if not value and secret_resolver is not None:
            try:
                value = secret_resolver(name) or ""
            except Exception as e:
                missing_detail = str(e)[:120] or missing_detail
        report.add(
            f"secret:{name}",
            passed=bool(value),
            detail="" if value else missing_detail,
        )

    # 2. Writable paths
    for raw_path in writable_paths:
        p = Path(raw_path)
        # If the caller handed us a file path, test its parent.
        target_dir = p if p.is_dir() or (not p.suffix and not p.exists()) else p.parent
        ok = _is_writable_dir(target_dir)
        report.add(
            f"writable:{raw_path}",
            passed=ok,
            detail="" if ok else "cannot create/write probe file",
        )

    # 3. Exchange connectivity
    if exchange_client is not None:
        try:
            latency_ms = float(exchange_client.ping())
            report.add(
                "exchange:ping",
                passed=latency_ms >= 0,
                detail=f"latency_ms={latency_ms:.1f}",
            )
        except Exception as e:
            report.add("exchange:ping", passed=False, detail=str(e)[:120])

    # 4. Environment consistency
    env_matches = expected_environment == config_environment
    report.add(
        "env:consistency",
        passed=env_matches,
        detail=(
            ""
            if env_matches
            else f"runner={expected_environment} config={config_environment}"
        ),
    )

    return report


def format_preflight(report: PreflightReport) -> str:
    """Render the preflight report for CLI output."""
    lines = ["=" * 60, "🛫 Preflight checks", "=" * 60]
    for check in report.checks:
        mark = "✅" if check["passed"] else "❌"
        detail = f" — {check['detail']}" if check["detail"] else ""
        lines.append(f"{mark} {check['name']}{detail}")
    lines.append("=" * 60)
    lines.append(
        "🟢 PREFLIGHT PASSED" if report.all_passed else "🔴 PREFLIGHT FAILED"
    )
    return "\n".join(lines)
