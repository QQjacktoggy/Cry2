"""Tests for bot.runtime.preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.runtime.preflight import run_preflight


class _DummyExchange:
    def __init__(self, latency: float = 42.0, err: Exception | None = None) -> None:
        self._latency = latency
        self._err = err

    def ping(self) -> float:
        if self._err:
            raise self._err
        return self._latency


def test_all_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
    report = run_preflight(
        required_secrets=["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"],
        writable_paths=[tmp_path / "runs", tmp_path / "journal.db"],
        exchange_client=_DummyExchange(),
        expected_environment="paper",
        config_environment="paper",
    )
    assert report.all_passed, report.checks


def test_missing_secret_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    report = run_preflight(
        required_secrets=["BINANCE_TESTNET_API_KEY"],
        writable_paths=[tmp_path],
        exchange_client=None,
        expected_environment="paper",
        config_environment="paper",
    )
    assert not report.all_passed
    secret_check = next(c for c in report.checks if c["name"].startswith("secret:"))
    assert secret_check["passed"] is False


def test_secret_resolver_can_satisfy_missing_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    observed: list[str] = []

    def _resolve(name: str) -> str:
        observed.append(name)
        return "from-secret-manager"

    report = run_preflight(
        required_secrets=["BINANCE_TESTNET_API_KEY"],
        writable_paths=[tmp_path],
        exchange_client=None,
        expected_environment="paper",
        config_environment="paper",
        secret_resolver=_resolve,
    )

    assert report.all_passed, report.checks
    assert observed == ["BINANCE_TESTNET_API_KEY"]


def test_env_mismatch_fails(tmp_path: Path) -> None:
    report = run_preflight(
        required_secrets=[],
        writable_paths=[tmp_path],
        exchange_client=None,
        expected_environment="paper",
        config_environment="live",
    )
    env_check = next(c for c in report.checks if c["name"] == "env:consistency")
    assert env_check["passed"] is False
    assert "paper" in env_check["detail"]
    assert "live" in env_check["detail"]


def test_exchange_ping_error_fails(tmp_path: Path) -> None:
    report = run_preflight(
        required_secrets=[],
        writable_paths=[tmp_path],
        exchange_client=_DummyExchange(err=RuntimeError("boom")),
        expected_environment="paper",
        config_environment="paper",
    )
    ping_check = next(c for c in report.checks if c["name"] == "exchange:ping")
    assert ping_check["passed"] is False
    assert "boom" in ping_check["detail"]
