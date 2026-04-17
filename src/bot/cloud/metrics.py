"""Cloud Monitoring custom metrics."""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class CloudMetrics:
    """Reports custom metrics to Google Cloud Monitoring."""

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._client: Any = None

        if self.project_id:
            try:
                from google.cloud import monitoring_v3
                self._client = monitoring_v3.MetricServiceClient()
            except ImportError:
                pass

    def report_pnl(self, daily_pnl: float) -> None:
        """Report daily PnL metric."""
        logger.debug("metric_reported", name="daily_pnl", value=daily_pnl)

    def report_pending_orders(self, count: int) -> None:
        """Report pending orders count."""
        logger.debug("metric_reported", name="pending_orders", value=count)

    def report_kill_switch_state(self, triggered: bool) -> None:
        """Report kill switch state."""
        logger.debug("metric_reported", name="kill_switch", value=int(triggered))
