"""Position sizing calculation.

Supports multiple sizing methods:
- Fixed fractional: risk fixed % of equity per trade
- ATR-based: size based on ATR for volatility-adjusted risk
- Kelly criterion: optimal sizing based on win rate and payoff
"""

from __future__ import annotations

from enum import Enum

import structlog

from bot.utils.math_utils import safe_divide, clamp

logger = structlog.get_logger(__name__)


class SizingMethod(str, Enum):
    """Position sizing methods."""
    FIXED_FRACTIONAL = "fixed_fractional"
    ATR_BASED = "atr_based"
    KELLY = "kelly"


class PositionSizer:
    """Calculates position sizes based on risk parameters."""

    def __init__(
        self,
        max_risk_per_trade_pct: float = 1.0,
        max_position_value_pct: float = 20.0,
        max_leverage: int = 3,
        hard_max_leverage: int = 5,
    ) -> None:
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_position_value_pct = max_position_value_pct
        self.max_leverage = max_leverage
        self.hard_max_leverage = hard_max_leverage

    def calculate_fixed_fractional(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        leverage: int = 1,
    ) -> float:
        """Calculate position size using fixed fractional method.

        Size = (Equity × Risk%) / |Entry - StopLoss| × Leverage

        Args:
            equity: Total account equity.
            entry_price: Expected entry price.
            stop_loss_price: Stop loss price.
            leverage: Leverage multiplier.

        Returns:
            Position size (quantity).
        """
        if entry_price <= 0 or equity <= 0:
            return 0.0

        risk_amount = equity * (self.max_risk_per_trade_pct / 100.0)
        price_risk = abs(entry_price - stop_loss_price)

        if price_risk <= 0:
            logger.warning("zero_price_risk", entry=entry_price, stop=stop_loss_price)
            return 0.0

        leverage = min(leverage, self.max_leverage, self.hard_max_leverage)
        quantity = (risk_amount / price_risk) * leverage

        # Cap by max position value
        max_value = equity * (self.max_position_value_pct / 100.0) * leverage
        max_quantity = max_value / entry_price
        quantity = min(quantity, max_quantity)

        logger.debug(
            "position_sized",
            method="fixed_fractional",
            equity=equity,
            risk_amount=risk_amount,
            quantity=quantity,
        )
        return quantity

    def calculate_atr_based(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 2.0,
        leverage: int = 1,
    ) -> float:
        """Calculate position size using ATR-based method.

        Size = (Equity × Risk%) / (ATR × Multiplier) × Leverage

        Args:
            equity: Total account equity.
            entry_price: Expected entry price.
            atr: Average True Range value.
            atr_multiplier: ATR multiplier for stop distance.
            leverage: Leverage multiplier.

        Returns:
            Position size (quantity).
        """
        if atr <= 0 or entry_price <= 0 or equity <= 0:
            return 0.0

        risk_amount = equity * (self.max_risk_per_trade_pct / 100.0)
        stop_distance = atr * atr_multiplier
        leverage = min(leverage, self.max_leverage, self.hard_max_leverage)

        quantity = (risk_amount / stop_distance) * leverage

        # Cap by max position value
        max_value = equity * (self.max_position_value_pct / 100.0) * leverage
        max_quantity = max_value / entry_price
        quantity = min(quantity, max_quantity)

        return quantity

    def calculate_kelly(
        self,
        equity: float,
        entry_price: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        leverage: int = 1,
        kelly_fraction: float = 0.25,
    ) -> float:
        """Calculate position size using Kelly criterion.

        Kelly% = (WinRate × AvgWin - (1-WinRate) × AvgLoss) / AvgWin
        Use fractional Kelly (default 25%) for safety.

        Args:
            equity: Total account equity.
            entry_price: Expected entry price.
            win_rate: Historical win rate (0-1).
            avg_win: Average winning trade %.
            avg_loss: Average losing trade % (positive number).
            leverage: Leverage multiplier.
            kelly_fraction: Fraction of Kelly to use (safety margin).

        Returns:
            Position size (quantity).
        """
        if avg_win <= 0 or entry_price <= 0 or equity <= 0:
            return 0.0

        kelly_pct = safe_divide(
            win_rate * avg_win - (1 - win_rate) * avg_loss,
            avg_win,
        )

        # Apply fractional Kelly and clamp
        kelly_pct = clamp(kelly_pct * kelly_fraction, 0.0, self.max_risk_per_trade_pct / 100.0)

        leverage = min(leverage, self.max_leverage, self.hard_max_leverage)
        position_value = equity * kelly_pct * leverage
        quantity = position_value / entry_price

        return max(quantity, 0.0)
