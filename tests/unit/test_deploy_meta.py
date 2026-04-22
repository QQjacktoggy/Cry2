"""Tests for bot.runtime.deploy_meta."""

from __future__ import annotations

import pytest

from bot.runtime.deploy_meta import build_deploy_meta


def test_fingerprint_stable_for_same_config() -> None:
    cfg = {"a": 1, "b": {"c": 2}}
    m1 = build_deploy_meta(config=cfg, version="v74", environment="paper")
    m2 = build_deploy_meta(config=cfg, version="v74", environment="paper")
    assert m1.config_fingerprint == m2.config_fingerprint


def test_fingerprint_changes_with_config() -> None:
    m1 = build_deploy_meta(config={"a": 1}, version="v74", environment="paper")
    m2 = build_deploy_meta(config={"a": 2}, version="v74", environment="paper")
    assert m1.config_fingerprint != m2.config_fingerprint


def test_fingerprint_stable_under_key_order() -> None:
    cfg1 = {"a": 1, "b": 2}
    cfg2 = {"b": 2, "a": 1}
    m1 = build_deploy_meta(config=cfg1, version="v74", environment="paper")
    m2 = build_deploy_meta(config=cfg2, version="v74", environment="paper")
    assert m1.config_fingerprint == m2.config_fingerprint


def test_run_id_unique_across_builds() -> None:
    m1 = build_deploy_meta(config={}, version="v74", environment="paper")
    m2 = build_deploy_meta(config={}, version="v74", environment="paper")
    assert m1.run_id != m2.run_id


def test_git_sha_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "deadbeefcafebabe12345")
    m = build_deploy_meta(config={}, version="v74", environment="paper")
    assert m.git_sha == "deadbeefcafe"


def test_image_tag_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_TAG", "testnet-2026-04-22")
    m = build_deploy_meta(config={}, version="v74", environment="paper")
    assert m.image_tag == "testnet-2026-04-22"


def test_environment_falls_back_to_config() -> None:
    m = build_deploy_meta(config={"environment": "paper"}, version="v74")
    assert m.environment == "paper"


def test_environment_override_wins() -> None:
    m = build_deploy_meta(
        config={"environment": "paper"},
        version="v74",
        environment="live",
    )
    assert m.environment == "live"
