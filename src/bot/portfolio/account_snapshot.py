"""Daily account snapshot persistence."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from bot.core.types import AccountSnapshot

logger = structlog.get_logger(__name__)


class SnapshotManager:
    """Manages daily account snapshots.

    Saves snapshots as JSON files for tracking account history.
    """

    def __init__(self, snapshot_dir: str = "./data/snapshots") -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots: list[AccountSnapshot] = []

    def save(self, snapshot: AccountSnapshot) -> None:
        """Save a snapshot to disk and memory."""
        self._snapshots.append(snapshot)

        # Save as JSON
        date_str = snapshot.timestamp.strftime("%Y-%m-%d")
        file_path = self.snapshot_dir / f"{date_str}.json"

        data = snapshot.model_dump(mode="json")
        # Convert datetime objects to ISO strings
        if isinstance(data.get("timestamp"), str):
            pass
        else:
            data["timestamp"] = snapshot.timestamp.isoformat()

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(
            "snapshot_saved",
            date=date_str,
            equity=snapshot.total_equity,
            positions=len(snapshot.positions),
        )

    def load_history(self) -> list[AccountSnapshot]:
        """Load all saved snapshots."""
        snapshots = []
        for file_path in sorted(self.snapshot_dir.glob("*.json")):
            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                snapshot = AccountSnapshot(**data)
                snapshots.append(snapshot)
            except Exception as e:
                logger.warning("snapshot_load_error", file=str(file_path), error=str(e))
        return snapshots

    def get_latest(self) -> AccountSnapshot | None:
        """Get the most recent snapshot."""
        if self._snapshots:
            return self._snapshots[-1]

        files = sorted(self.snapshot_dir.glob("*.json"))
        if not files:
            return None

        try:
            with open(files[-1], encoding="utf-8") as f:
                data = json.load(f)
            return AccountSnapshot(**data)
        except Exception:
            return None
