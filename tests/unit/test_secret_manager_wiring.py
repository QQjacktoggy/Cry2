"""Tests for the GCP Secret Manager fallthrough in bot.config.env."""

from __future__ import annotations

import pytest

from bot.config import env as env_module
from bot.core.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    env_module._reset_secret_manager_for_tests()
    # Default: not on GCP.
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    yield
    env_module._reset_secret_manager_for_tests()


def test_env_value_returned_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert env_module.get_secret("MY_SECRET") == "from-env"


def test_missing_raises_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(ConfigError):
        env_module.get_secret("MISSING_SECRET", required=True)


def test_missing_returns_empty_when_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    assert env_module.get_secret("MISSING_SECRET", required=False) == ""


def test_delegates_to_secret_manager_on_gcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.delenv("GCP_SECRET_VAR", raising=False)

    class _FakeManager:
        def __init__(self, project_id: str) -> None:
            self.project_id = project_id

        def get_secret(self, name: str) -> str:
            assert name == "gcp-secret-var"
            return "from-gcp"

    monkeypatch.setattr(env_module, "SecretManager", _FakeManager, raising=False)

    # The module lazy-imports SecretManager, so patch the import site too.
    import bot.cloud.secret_manager as sm_mod

    monkeypatch.setattr(sm_mod, "SecretManager", _FakeManager)

    value = env_module.get_secret("GCP_SECRET_VAR")
    assert value == "from-gcp"
    # The fetched secret is cached into env for subsequent lookups.
    import os

    assert os.environ["GCP_SECRET_VAR"] == "from-gcp"


def test_secret_name_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("SECRET_NAME_FOR_CUSTOM_VAR", "override-name")
    monkeypatch.delenv("CUSTOM_VAR", raising=False)

    observed: list[str] = []

    class _FakeManager:
        def __init__(self, project_id: str) -> None:
            pass

        def get_secret(self, name: str) -> str:
            observed.append(name)
            return "ok"

    import bot.cloud.secret_manager as sm_mod

    monkeypatch.setattr(sm_mod, "SecretManager", _FakeManager)

    env_module.get_secret("CUSTOM_VAR")
    assert observed == ["override-name"]
