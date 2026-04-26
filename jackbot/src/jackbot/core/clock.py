"""UTC clock abstraction for testability."""

from __future__ import annotations

from datetime import UTC, datetime


class Clock:
    """Real UTC clock (swap with mock in tests)."""

    def now(self) -> datetime:
        return datetime.now(UTC)
