"""Cloud Monitoring custom metrics.

Off GCP, ``CloudMetrics`` is a no-op that still logs every metric to
structlog; this is enough for local observability and unit tests
without requiring the SDK to be installed. On GCP, the same calls
also emit Cloud Monitoring time series when ``report_pnl`` etc. are
invoked.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class CloudMetrics:
    """Reports custom metrics to Google Cloud Monitoring (best-effort)."""

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = (
            project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        ).strip()
        self._client: Any = None

        if self.project_id:
            try:
                from google.cloud import monitoring_v3  # type: ignore

                self._client = monitoring_v3.MetricServiceClient()
            except ImportError:
                pass

    @property
    def enabled(self) -> bool:
        return bool(self._client and self.project_id)

    # ── individual metrics ───────────────────────────────────────────

    def report_pnl(self, daily_pnl: float) -> None:
        self._report("daily_pnl", float(daily_pnl))

    def report_pending_orders(self, count: int) -> None:
        self._report("pending_orders", float(count))

    def report_kill_switch_state(self, triggered: bool) -> None:
        self._report("kill_switch", float(triggered))

    def report_ws_connected(self, connected: bool) -> None:
        self._report("ws_connected", float(connected))

    def report_api_latency(self, latency_ms: float) -> None:
        self._report("api_latency_ms", float(latency_ms))

    def report_drawdown_halt(self, active: bool) -> None:
        self._report("drawdown_halt", float(active))

    # ── implementation ──────────────────────────────────────────────

    def _report(self, name: str, value: float) -> None:
        """Log every metric; also push to Cloud Monitoring when enabled.

        Cloud Monitoring pushes are best-effort: failures log a warning
        but never raise, because a metrics outage must not kill the
        trading loop.
        """
        logger.debug("metric_reported", name=name, value=value)
        if not self.enabled:
            return
        try:  # pragma: no cover - SDK surface
            from google.cloud import monitoring_v3  # type: ignore

            project_name = f"projects/{self.project_id}"
            series = monitoring_v3.TimeSeries()
            series.metric.type = f"custom.googleapis.com/bot/{name}"
            series.resource.type = "global"
            now = time.time()
            point = monitoring_v3.Point(
                {
                    "interval": {"end_time": {"seconds": int(now)}},
                    "value": {"double_value": float(value)},
                }
            )
            series.points = [point]
            self._client.create_time_series(
                name=project_name, time_series=[series]
            )
        except Exception as e:  # pragma: no cover
            logger.warning("metric_push_failed", name=name, error=str(e))
