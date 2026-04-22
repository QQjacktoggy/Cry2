"""Cloud Logging integration for structlog.

On GCP, :func:`setup_cloud_logging` initializes a dedicated Cloud Logging
structured logger. ``bot.core.logger`` forwards every structlog event
dict to :func:`emit_structured_log`, so Cloud Logging receives
queryable ``jsonPayload`` records instead of rendered ``textPayload``.
Off GCP, setup is a no-op so local development does not require the SDK.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)
_STRUCTURED_CLOUD_LOGGER: Any | None = None
_STDLIB_CLOUD_HANDLER: logging.Handler | None = None
_APP_LOGGER_PREFIXES = ("bot.", "scripts.", "__main__")


class _ThirdPartyOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "bot":
            return False
        return not any(record.name.startswith(prefix) for prefix in _APP_LOGGER_PREFIXES)


def _create_stdlib_cloud_handler(client: Any) -> logging.Handler:
    if hasattr(client, "get_default_handler"):
        try:
            handler = client.get_default_handler(name="cry2-stdlib")
        except TypeError:
            handler = client.get_default_handler()
    else:
        from google.cloud.logging_v2.handlers import CloudLoggingHandler  # type: ignore

        handler = CloudLoggingHandler(client, name="cry2-stdlib")
    handler.addFilter(_ThirdPartyOnlyFilter())
    return handler


def _attach_stdlib_cloud_handler(client: Any) -> None:
    global _STDLIB_CLOUD_HANDLER

    root_logger = logging.getLogger()
    if _STDLIB_CLOUD_HANDLER is not None:
        root_logger.removeHandler(_STDLIB_CLOUD_HANDLER)
    _STDLIB_CLOUD_HANDLER = _create_stdlib_cloud_handler(client)
    root_logger.addHandler(_STDLIB_CLOUD_HANDLER)


def setup_cloud_logging(project_id: str | None = None) -> bool:
    """Initialize the Cloud Logging structured logger if project is available.

    Returns ``True`` when the structured logger was initialized successfully.
    """
    global _STRUCTURED_CLOUD_LOGGER
    project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        return False

    try:
        import google.cloud.logging  # type: ignore

        client = google.cloud.logging.Client(project=project)
        _STRUCTURED_CLOUD_LOGGER = client.logger("cry2-runtime")
        _attach_stdlib_cloud_handler(client)
        logger.info("cloud_logging_enabled", project=project)
        return True
    except ImportError:
        logger.info("cloud_logging_sdk_not_available")
    except Exception as e:  # pragma: no cover - SDK surface
        logger.warning("cloud_logging_setup_failed", error=str(e))
    return False


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, os.PathLike):
        return os.fspath(value).replace("\\", "/")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def emit_structured_log(event_dict: dict[str, Any]) -> None:
    """Send a structured copy of a structlog event to Cloud Logging."""
    cloud_logger = _STRUCTURED_CLOUD_LOGGER
    if cloud_logger is None:
        return

    payload = {key: _sanitize_payload(value) for key, value in event_dict.items()}
    severity = str(payload.get("level", "INFO")).upper()
    cloud_logger.log_struct(payload, severity=severity)


def emit_lifecycle_event(event: str, /, **fields: Any) -> None:
    """Log a well-known runtime lifecycle event.

    Cloud Logging + local structlog both receive the same structured
    record. Callers supply ``run_id`` / ``environment`` / ``version`` as
    fields so downstream dashboards can filter per-run.
    """
    logger.info(f"lifecycle.{event}", **fields)
