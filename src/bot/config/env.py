"""Environment variable loading and secret resolution.

Local development reads ``.env`` files via python-dotenv. In GCP
(detected by ``GOOGLE_CLOUD_PROJECT``) we delegate to Secret Manager
and cache the resolved values into ``os.environ`` so downstream code
that still reads env vars (e.g. ``os.environ["BINANCE_API_KEY"]``) keeps
working untouched.

The mapping from env var name to Secret Manager secret ID defaults to
a lowercase, dash-separated variant (``BINANCE_API_KEY`` →
``binance-api-key``), mirroring the convention in ``DEPLOYMENT_GCP.md``.
An explicit override can be supplied via ``SECRET_NAME_FOR_<ENV_VAR>``
environment variables, which is useful for per-environment secret
rotation.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from dotenv import load_dotenv

from bot.core.exceptions import ConfigError

logger = structlog.get_logger(__name__)

_SECRET_MANAGER: object | None = None
_SECRET_MANAGER_INITIALIZED = False


def _env_to_secret_id(env_var: str) -> str:
    """Map ``BINANCE_API_KEY`` → ``binance-api-key`` unless overridden."""
    override = os.environ.get(f"SECRET_NAME_FOR_{env_var}")
    if override:
        return override
    return env_var.lower().replace("_", "-")


def _get_secret_manager() -> object | None:
    """Return a cached ``SecretManager`` instance when on GCP; ``None`` otherwise."""
    global _SECRET_MANAGER, _SECRET_MANAGER_INITIALIZED
    if _SECRET_MANAGER_INITIALIZED:
        return _SECRET_MANAGER

    _SECRET_MANAGER_INITIALIZED = True
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        return None

    try:
        from bot.cloud.secret_manager import SecretManager

        _SECRET_MANAGER = SecretManager(project_id=project)
    except Exception as e:  # pragma: no cover - defensive for missing SDK
        logger.warning("secret_manager_init_failed", error=str(e))
        _SECRET_MANAGER = None
    return _SECRET_MANAGER


def load_env(env_file: str | Path | None = None) -> None:
    """Load environment variables.

    * On GCP (``GOOGLE_CLOUD_PROJECT`` set): no-op here — secrets are
      pulled lazily in :func:`get_secret` and cached into the process
      environment.
    * Locally: read ``.env`` / ``.env.testnet``.
    """
    gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if gcp_project:
        logger.info("gcp_environment_detected", project=gcp_project)
        return

    if env_file:
        env_path = Path(env_file)
    else:
        for candidate in [".env", ".env.testnet"]:
            candidate_path = Path(candidate)
            if candidate_path.exists():
                env_path = candidate_path
                break
        else:
            logger.warning("no_env_file_found", searched=[".env", ".env.testnet"])
            return

    if not env_path.exists():
        raise ConfigError(f"Environment file not found: {env_path}")

    load_dotenv(env_path, override=False)
    logger.info("env_loaded", path=str(env_path))


def get_secret(env_var: str, required: bool = True) -> str:
    """Resolve a secret by env-var name.

    Resolution order:

    1. If the env var is already populated (local ``.env`` or a previous
       Secret Manager lookup), return it.
    2. If ``GOOGLE_CLOUD_PROJECT`` is set, fetch from Secret Manager and
       cache back into ``os.environ``.
    3. Raise / return empty according to ``required``.
    """
    value = os.environ.get(env_var, "").strip()
    if value:
        return value

    manager = _get_secret_manager()
    if manager is not None:
        secret_id = _env_to_secret_id(env_var)
        try:
            fetched = manager.get_secret(secret_id) or ""  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover - surfaced via required check
            logger.warning(
                "secret_manager_fetch_failed",
                env_var=env_var,
                secret_id=secret_id,
                error=str(e),
            )
            fetched = ""
        fetched = fetched.strip()
        if fetched:
            os.environ[env_var] = fetched
            logger.debug("secret_loaded_from_gcp", env_var=env_var, secret_id=secret_id)
            return fetched

    if required:
        raise ConfigError(
            f"Required secret '{env_var}' not set. "
            f"Provide it via .env file or GCP Secret Manager "
            f"(secret id: {_env_to_secret_id(env_var)})."
        )
    return ""


def _reset_secret_manager_for_tests() -> None:
    """Clear cached Secret Manager state. Intended for unit tests only."""
    global _SECRET_MANAGER, _SECRET_MANAGER_INITIALIZED
    _SECRET_MANAGER = None
    _SECRET_MANAGER_INITIALIZED = False
