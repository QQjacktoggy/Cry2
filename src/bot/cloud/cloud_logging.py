"""Cloud Logging integration for structlog."""

from __future__ import annotations

import logging
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def setup_cloud_logging(project_id: str | None = None) -> None:
    """Set up Google Cloud Logging handler alongside local logging."""
    if not project_id:
        return

    try:
        import google.cloud.logging
        client = google.cloud.logging.Client(project=project_id)
        client.setup_logging(log_level=logging.INFO)
        logger.info("cloud_logging_enabled", project=project_id)
    except ImportError:
        logger.info("cloud_logging_sdk_not_available")
    except Exception as e:
        logger.warning("cloud_logging_setup_failed", error=str(e))
