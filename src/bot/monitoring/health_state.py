"""Runtime health snapshot writer for the monitoring dashboard."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class HealthStateWriter:
    """Persist live runtime state to ``data/health.json``."""

    def __init__(self, path: str | Path = "./data/health.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}

    def update(self, **changes: Any) -> None:
        """Merge updates into the current state and write them to disk."""
        with self._lock:
            self._state.update(changes)
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._state, indent=2, default=str),
                encoding="utf-8",
            )
            tmp_path.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the latest in-memory state."""
        with self._lock:
            return dict(self._state)
