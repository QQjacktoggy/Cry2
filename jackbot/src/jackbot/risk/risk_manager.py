"""Risk Manager for Jackbot_V1.

Simplified risk management focused on grid day-trading:
  - Daily loss limit
  - BTC crash detection
  - Max concurrent grids
  - Conservative mode integration
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from jackbot.core.clock import Clock

logger = structlog.get_logger(__name__)


class RiskManager:
    """Risk management for grid day-trading."""

    def __init__(
        self,
        daily_loss_limit_usd: float = 15.0,
        btc_crash_threshold_pct: float = 5.0,
        max_concurrent_grids: int = 2,
        clock: Clock | None = None,
    ) -> None:
        self._daily_loss_limit = daily_loss_limit_usd
        self._btc_crash_pct = btc_crash_threshold_pct / 100.0
        self._max_grids = max_concurrent_grids
        self._clock = clock or Clock()

        self._daily_loss: float = 0.0
        self._halted: bool = False
        self._halt_reason: str = ""
        self._last_reset_date: str = ""

    @property
    def is_trading_allowed(self) -> bool:
        return not self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def check(self, active_grid_count: int) -> bool:
        """Run all risk checks. Returns True if trading is allowed."""
        self._check_daily_reset()

        if self._halted:
            return False

        if active_grid_count >= self._max_grids:
            self._halt_reason = f"max_grids_reached ({active_grid_count}/{self._max_grids})"
            return False

        if self._daily_loss >= self._daily_loss_limit:
            self._halted = True
            self._halt_reason = f"daily_loss_limit ({self._daily_loss:.2f}/{self._daily_loss_limit:.2f})"
            logger.error("risk_halt", reason=self._halt_reason)
            return False

        self._halt_reason = ""
        return True

    def record_loss(self, loss_usd: float) -> None:
        """Record a loss (positive value = loss amount)."""
        if loss_usd > 0:
            self._daily_loss += loss_usd

    def check_btc_crash(self, prev_close: float, current_close: float) -> bool:
        """Check if BTC has crashed. Returns True if crash detected."""
        if prev_close <= 0:
            return False
        change = (current_close - prev_close) / prev_close
        if change <= -self._btc_crash_pct:
            self._halted = True
            self._halt_reason = f"btc_crash ({change * 100:.2f}%)"
            logger.error("risk_halt_btc_crash", change_pct=f"{change * 100:.2f}%")
            return True
        return False

    def _check_daily_reset(self) -> None:
        today = self._clock.now().strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._daily_loss = 0.0
            self._halted = False
            self._halt_reason = ""
            self._last_reset_date = today

    def get_status(self) -> dict:
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "daily_loss": round(self._daily_loss, 4),
            "daily_loss_limit": self._daily_loss_limit,
        }
