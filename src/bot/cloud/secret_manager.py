"""GCP Secret Manager integration.

Production: reads secrets from GCP Secret Manager.
Development: falls back to .env file.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class SecretManager:
    """GCP Secret Manager wrapper with local fallback."""

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._client: Any = None

        if self.project_id:
            try:
                from google.cloud import secretmanager
                self._client = secretmanager.SecretManagerServiceClient()
                logger.info("gcp_secret_manager_initialized", project=self.project_id)
            except ImportError:
                logger.info("gcp_sdk_not_installed_using_env_fallback")

    def get_secret(self, secret_name: str, version: str = "latest") -> str:
        """Get a secret value.

        Tries GCP Secret Manager first, falls back to environment variables.
        """
        if self._client and self.project_id:
            try:
                name = f"projects/{self.project_id}/secrets/{secret_name}/versions/{version}"
                response = self._client.access_secret_version(name=name)
                return response.payload.data.decode("UTF-8")
            except Exception as e:
                logger.warning("gcp_secret_fetch_failed", secret=secret_name, error=str(e))

        # Fallback to environment variable
        env_key = secret_name.upper().replace("-", "_")
        value = os.environ.get(env_key, "")
        if not value:
            logger.warning("secret_not_found", name=secret_name, env_key=env_key)
        return value
