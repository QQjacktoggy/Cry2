"""API rate limiter to avoid Binance rate limits.

Binance futures: 2400 request weight per minute.
"""

from __future__ import annotations

import time
import threading
from collections import deque

import structlog

from bot.core.exceptions import RateLimitError

logger = structlog.get_logger(__name__)


class RateLimiter:
    """Token bucket rate limiter for API calls."""

    def __init__(
        self,
        max_weight: int = 2400,
        window_seconds: int = 60,
    ) -> None:
        self.max_weight = max_weight
        self.window_seconds = window_seconds
        self._requests: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1) -> None:
        """Acquire rate limit tokens. Blocks if limit would be exceeded."""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds

            # Remove expired entries
            while self._requests and self._requests[0][0] < cutoff:
                self._requests.popleft()

            current_weight = sum(w for _, w in self._requests)

            if current_weight + weight > self.max_weight:
                # Wait until oldest request expires
                if self._requests:
                    wait_time = self._requests[0][0] + self.window_seconds - now
                    if wait_time > 0:
                        logger.warning("rate_limit_wait", wait_seconds=wait_time)
                        time.sleep(wait_time)

            self._requests.append((time.time(), weight))

    @property
    def current_weight(self) -> int:
        """Get current request weight in the window."""
        now = time.time()
        cutoff = now - self.window_seconds
        return sum(w for t, w in self._requests if t >= cutoff)

    @property
    def remaining_weight(self) -> int:
        """Get remaining weight capacity."""
        return max(0, self.max_weight - self.current_weight)
