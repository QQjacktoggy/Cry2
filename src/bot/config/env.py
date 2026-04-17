"""Environment variable loading from .env files."""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from dotenv import load_dotenv

from bot.core.exceptions import ConfigError

logger = structlog.get_logger(__name__)


def load_env(env_file: str | Path | None = None) -> None:
    """Load environment variables from .env file.

    Checks for GCP Secret Manager first (production).
    Falls back to .env file (development).

    Args:
        env_file: Explicit path to .env file. If None, auto-detect.
    """
    # Check if running on GCP (has GOOGLE_CLOUD_PROJECT set)
    gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if gcp_project:
        logger.info("gcp_environment_detected", project=gcp_project)
        # Secrets will be loaded via cloud/secret_manager.py
        return

    # Local development - load from .env
    if env_file:
        env_path = Path(env_file)
    else:
        # Auto-detect .env file
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
    """Get a secret value from environment.

    Args:
        env_var: Environment variable name.
        required: If True, raise error when not found.

    Returns:
        Secret value.
    """
    value = os.environ.get(env_var)
    if value is None and required:
        raise ConfigError(
            f"Required environment variable '{env_var}' not set. "
            f"Set it in .env file or GCP Secret Manager."
        )
    return value or ""
