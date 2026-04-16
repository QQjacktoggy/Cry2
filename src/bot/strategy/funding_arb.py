"""Strategy A: Funding Rate Arbitrage.

When funding rate annualized > 15%: short futures (+ manual long spot)
When funding rate annualized < 5%: exit
Max hold: 7 days
v1: Only futures side, spot side is manual.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

from bot.core.constants import OrderSide, OrderType, PositionSide
from bot.core.events import FillEvent, FundingEvent, MarketEvent, SignalEvent
from bot.core.types import Position
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class FundingArbStrategy(BaseStrategy):
    """Funding rate arbitrage strategy."""

    name = "funding_arb"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.funding_threshold_annual = params.get("funding_rate_threshold_annual", 15.0)
        self.funding_exit_annual = params.get("funding_rate_exit_annual", 5.0)
        self.max_hold_days = params.get("max_hold_days", 7)
        self.max_exposure_pct = params.get("max_exposure_pct", 20.0)

        # State
        self._last_funding_rates: dict[str, float] = {}
        self._entry_times: dict[str, datetime] = {}

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Check funding rate and generate entry/exit signals."""
        self._record_bar(event)
        signals: list[SignalEvent] = []
        symbol = event.symbol
        pos = self._get_position(symbol)

        funding_rate = self._last_funding_rates.get(symbol, 0.0)
        annual_rate = funding_rate * 3 * 365 * 100  # annualized %

        if pos.is_open:
            # Check exit conditions
            should_exit = False
            reason = ""

            if abs(annual_rate) < self.funding_exit_annual:
                should_exit = True
                reason = f"Funding rate normalized: {annual_rate:.2f}%"

            if symbol in self._entry_times:
                hold_time = event.timestamp - self._entry_times[symbol]
                if hold_time >= timedelta(days=self.max_hold_days):
                    should_exit = True
                    reason = f"Max hold time {self.max_hold_days}d reached"

            if should_exit:
                side = OrderSide.BUY if pos.side == PositionSide.SHORT else OrderSide.SELL
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=side,
                    quantity=pos.quantity,
                    timestamp=event.timestamp,
                    reduce_only=True,
                    reason=reason,
                ))
        else:
            # Check entry conditions (only positive funding = short futures)
            if annual_rate > self.funding_threshold_annual:
                # Calculate position size (simplified)
                quantity = self._calculate_size(event.close)
                if quantity > 0:
                    signals.append(self._create_signal(
                        symbol=symbol,
                        side=OrderSide.SELL,  # Short futures
                        quantity=quantity,
                        timestamp=event.timestamp,
                        reason=f"High funding rate: {annual_rate:.2f}% annualized",
                    ))
                    self._entry_times[symbol] = event.timestamp

        return signals

    def on_funding(self, event: FundingEvent) -> None:
        """Update latest funding rate."""
        self._last_funding_rates[event.symbol] = event.funding_rate

    def _calculate_size(self, price: float) -> float:
        """Simplified position sizing."""
        if price <= 0:
            return 0.0
        # Use a nominal small position for now
        notional = 100.0  # $100 position
        return notional / price

    def warmup_bars(self) -> int:
        return 1
