"""Slippage models for backtest simulation.

Two models supported:
- Fixed BPS: constant basis points slippage
- Volume-weighted: slippage increases with trade size relative to bar volume
"""

from __future__ import annotations

from enum import Enum

import structlog

from bot.core.constants import OrderSide

logger = structlog.get_logger(__name__)


class SlippageModelType(str, Enum):
    """Slippage model types."""
    FIXED_BPS = "fixed_bps"
    VOLUME_WEIGHTED = "volume_weighted"


class SlippageModel:
    """Calculates slippage for simulated order fills."""

    def __init__(
        self,
        model_type: str = "fixed_bps",
        fixed_bps: float = 2.0,
        bps_base: float = 1.0,
        volume_impact_factor: float = 0.1,
    ) -> None:
        """Initialize slippage model.

        Args:
            model_type: 'fixed_bps' or 'volume_weighted'.
            fixed_bps: Fixed slippage in basis points (for fixed model).
            bps_base: Base slippage bps (for volume-weighted model).
            volume_impact_factor: How much volume ratio affects slippage.
        """
        self.model_type = SlippageModelType(model_type)
        self.fixed_bps = fixed_bps
        self.bps_base = bps_base
        self.volume_impact_factor = volume_impact_factor

    def calculate(
        self,
        price: float,
        side: OrderSide,
        quantity: float = 0.0,
        bar_volume: float = 0.0,
    ) -> float:
        """Calculate fill price after slippage.

        Args:
            price: Base price (typically next bar open).
            side: BUY or SELL.
            quantity: Order quantity.
            bar_volume: Bar volume for volume-weighted model.

        Returns:
            Adjusted fill price.
        """
        if self.model_type == SlippageModelType.FIXED_BPS:
            slippage_pct = self.fixed_bps / 10000.0
        else:
            # Volume-weighted: base + impact * (trade_size / bar_volume)
            base_pct = self.bps_base / 10000.0
            if bar_volume > 0 and quantity > 0:
                volume_ratio = quantity / bar_volume
                impact = self.volume_impact_factor * volume_ratio
            else:
                impact = 0.0
            slippage_pct = base_pct + impact

        # Buy orders pay more, sell orders receive less
        if side == OrderSide.BUY:
            return price * (1.0 + slippage_pct)
        else:
            return price * (1.0 - slippage_pct)
