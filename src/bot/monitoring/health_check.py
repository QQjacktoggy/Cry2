"""Health check - monitors WebSocket heartbeat and API latency."""

from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class HealthCheck:
    """Monitors system health."""

    def __init__(
        self,
        max_api_latency_ms: float = 3000.0,
        ws_timeout_sec: float = 60.0,
    ) -> None:
        self.max_api_latency_ms = max_api_latency_ms
        self.ws_timeout_sec = ws_timeout_sec

        self._last_ws_heartbeat: float = time.time()
        self._last_api_latency: float = 0.0
        self._api_latency_history: list[float] = []
        self._errors: list[dict[str, Any]] = []

    def record_ws_heartbeat(self) -> None:
        """Record WebSocket heartbeat."""
        self._last_ws_heartbeat = time.time()

    def record_api_latency(self, latency_ms: float) -> None:
        """Record an API call latency."""
        self._last_api_latency = latency_ms
        self._api_latency_history.append(latency_ms)
        if len(self._api_latency_history) > 100:
            self._api_latency_history = self._api_latency_history[-100:]

    def record_error(self, error: str) -> None:
        """Record an error occurrence."""
        self._errors.append({"time": time.time(), "error": error})
        if len(self._errors) > 100:
            self._errors = self._errors[-100:]

    def is_ws_healthy(self) -> bool:
        """Check if WebSocket connection is healthy."""
        elapsed = time.time() - self._last_ws_heartbeat
        return elapsed < self.ws_timeout_sec

    def is_api_healthy(self) -> bool:
        """Check if API latency is acceptable."""
        return self._last_api_latency < self.max_api_latency_ms

    def is_healthy(self) -> bool:
        """Overall health check."""
        return self.is_ws_healthy() and self.is_api_healthy()

    def get_status(self) -> dict[str, Any]:
        """Get comprehensive health status."""
        ws_elapsed = time.time() - self._last_ws_heartbeat
        avg_latency = (
            sum(self._api_latency_history) / len(self._api_latency_history)
            if self._api_latency_history
            else 0.0
        )

        return {
            "healthy": self.is_healthy(),
            "ws_healthy": self.is_ws_healthy(),
            "ws_last_heartbeat_sec": round(ws_elapsed, 1),
            "api_healthy": self.is_api_healthy(),
            "api_last_latency_ms": round(self._last_api_latency, 1),
            "api_avg_latency_ms": round(avg_latency, 1),
            "recent_errors": len(self._errors),
        }
