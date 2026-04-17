"""Google Cloud Storage backup utility."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class GCSBackup:
    """Backup data and logs to Google Cloud Storage."""

    def __init__(
        self,
        bucket_name: str = "",
        data_dir: str = "./data",
        logs_dir: str = "./logs",
    ) -> None:
        self.bucket_name = bucket_name or os.environ.get("GCS_BACKUP_BUCKET", "")
        self.data_dir = data_dir
        self.logs_dir = logs_dir

    def backup(self) -> bool:
        """Run backup of data and logs to GCS."""
        if not self.bucket_name:
            logger.warning("no_gcs_bucket_configured")
            return False

        success = True
        for local_dir, remote_prefix in [(self.data_dir, "data"), (self.logs_dir, "logs")]:
            if not Path(local_dir).exists():
                continue
            target = f"gs://{self.bucket_name}/{remote_prefix}/"
            try:
                result = subprocess.run(
                    ["gsutil", "-m", "rsync", "-r", local_dir, target],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
                if result.returncode == 0:
                    logger.info("backup_success", dir=local_dir, target=target)
                else:
                    logger.error("backup_failed", dir=local_dir, stderr=result.stderr[:200])
                    success = False
            except FileNotFoundError:
                logger.error("gsutil_not_found")
                return False
            except subprocess.TimeoutExpired:
                logger.error("backup_timeout", dir=local_dir)
                success = False

        return success
