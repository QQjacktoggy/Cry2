"""Funding rate settlement model.

Simulates 8-hour funding rate payments for perpetual futures.
Positive rate: longs pay shorts.
Negative rate: shorts pay longs.
"""

from __future__ import annotations

import structlog

from bot.core.constants import PositionSide

logger = structlog.get_logger(__name__)


class FundingModel:
    """Calculates funding rate payments."""

    def calculate(
        self,
        position_side: PositionSide,
        position_value: float,
        funding_rate: float,
    ) -> float:
        """Calculate funding payment for a position.

        Args:
            position_side: LONG or SHORT.
            position_value: Notional value of position.
            funding_rate: Current funding rate (e.g., 0.0001 = 0.01%).

        Returns:
            Funding payment (negative = you pay, positive = you receive).
        """
        if position_side == PositionSide.FLAT or position_value == 0:
            return 0.0

        # Positive funding rate: longs pay shorts
        # Negative funding rate: shorts pay longs
        payment = position_value * funding_rate

        if position_side == PositionSide.LONG:
            # Long pays when rate is positive (payment is negative for long)
            return -payment
        else:
            # Short receives when rate is positive (payment is positive for short)
            return payment

    @staticmethod
    def annualized_rate(funding_rate: float) -> float:
        """Convert per-period rate to annualized percentage.

        3 settlements per day * 365 days.
        """
        return funding_rate * 3 * 365 * 100

    @staticmethod
    def is_extreme(funding_rate: float, threshold_annual: float = 15.0) -> bool:
        """Check if funding rate is extreme (worth arbitraging).

        Args:
            funding_rate: Per-period rate.
            threshold_annual: Annualized threshold percentage.
        """
        annual = abs(funding_rate) * 3 * 365 * 100
        return annual > threshold_annual
