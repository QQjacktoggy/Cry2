"""Strategy Lab #1 — Perp-Spot Basis Arbitrage.

Market-neutral strategy that captures the basis spread between perpetual
futures and spot prices, plus the funding rate income.

Logic:
    When annualized_basis + annualized_funding > entry_threshold:
        - Short perp + Long spot (for positive basis)
    When signal reverses OR basis normalizes OR max_hold reached:
        - Close both legs

This is a SKELETON. Production deployment requires:
    1. Spot market connection (not present in V7.2 — which is perp-only)
    2. Paired order execution with atomic rollback
    3. Independent risk manager (separate from V7.2's leverage cap)
    4. Borrow/lending logic for inverse basis scenarios

NOT REGISTERED in register_default_strategies(). Must be loaded via
`strategies_lab.register_lab.register_lab_strategies()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog

from bot.core.constants import OrderSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class PerpSpotBasisArbStrategy(BaseStrategy):
    """Perp-Spot basis arbitrage strategy (lab prototype)."""

    name = "lab_perp_spot_basis"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.entry_threshold_annual = params.get("entry_threshold_annual", 15.0)
        self.exit_threshold_annual = params.get("exit_threshold_annual", 3.0)
        self.max_hold_days = params.get("max_hold_days", 7)
        self.max_basis_stop_annual = params.get("max_basis_stop_annual", 60.0)
        self.perp_leverage = params.get("perp_leverage", 2)

        self._entry_times: dict[str, datetime] = {}
        self._entry_basis: dict[str, float] = {}
        self._spot_prices: dict[str, float] = {}
        self._funding_rates: dict[str, float] = {}

    def inject_spot_price(self, symbol: str, price: float) -> None:
        """Lab hack: feed spot price from an external lab_data layer.

        In production, this should come through a dedicated Spot event type.
        """
        self._spot_prices[symbol] = price

    def inject_funding_rate(self, symbol: str, rate: float) -> None:
        """Lab hack: feed next funding rate estimate."""
        self._funding_rates[symbol] = rate

    def _compute_basis_annualized(self, symbol: str, perp_price: float) -> float:
        spot = self._spot_prices.get(symbol)
        if spot is None or spot <= 0:
            return 0.0
        basis_pct = (perp_price - spot) / spot
        # Assume basis decays over ~30 days → annualize by 365/30
        return basis_pct * (365.0 / 30.0) * 100.0

    def _compute_funding_annualized(self, symbol: str) -> float:
        rate = self._funding_rates.get(symbol, 0.0)
        return rate * 3.0 * 365.0 * 100.0

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        self._record_bar(event)
        signals: list[SignalEvent] = []
        symbol = event.symbol
        pos = self._get_position(symbol)

        basis_ann = self._compute_basis_annualized(symbol, event.close)
        funding_ann = self._compute_funding_annualized(symbol)
        total_carry = basis_ann + funding_ann

        if pos.is_open:
            should_exit = False
            reason = ""
            if abs(total_carry) < self.exit_threshold_annual:
                should_exit, reason = True, f"carry normalized: {total_carry:.2f}%"
            if symbol in self._entry_times:
                if event.timestamp - self._entry_times[symbol] >= timedelta(days=self.max_hold_days):
                    should_exit, reason = True, f"max hold {self.max_hold_days}d"
            entry_basis = self._entry_basis.get(symbol, 0.0)
            if abs(basis_ann - entry_basis) > self.max_basis_stop_annual:
                should_exit, reason = True, f"basis blowout: {basis_ann:.2f}%"

            if should_exit:
                side = OrderSide.BUY if pos.side.value == "short" else OrderSide.SELL
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=side,
                    quantity=pos.quantity,
                    timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"[LAB-BASIS] exit: {reason}",
                    metadata={"basis_ann": basis_ann, "funding_ann": funding_ann},
                ))
                self._entry_times.pop(symbol, None)
                self._entry_basis.pop(symbol, None)
        else:
            if total_carry > self.entry_threshold_annual:
                # Positive basis → short perp (spot long is external, see TODO)
                qty = self._calc_perp_size(event.close)
                if qty > 0:
                    signals.append(self._create_signal(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=qty,
                        timestamp=event.timestamp,
                        reason=f"[LAB-BASIS] enter short perp, carry={total_carry:.2f}%",
                        metadata={
                            "basis_ann": basis_ann,
                            "funding_ann": funding_ann,
                            "spot_leg_required": True,  # external hook
                        },
                    ))
                    self._entry_times[symbol] = event.timestamp
                    self._entry_basis[symbol] = basis_ann
            elif total_carry < -self.entry_threshold_annual:
                # Negative basis → long perp (spot short via borrow is external)
                qty = self._calc_perp_size(event.close)
                if qty > 0:
                    signals.append(self._create_signal(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=qty,
                        timestamp=event.timestamp,
                        reason=f"[LAB-BASIS] enter long perp, carry={total_carry:.2f}%",
                        metadata={
                            "basis_ann": basis_ann,
                            "funding_ann": funding_ann,
                            "spot_leg_required": True,
                        },
                    ))
                    self._entry_times[symbol] = event.timestamp
                    self._entry_basis[symbol] = basis_ann

        return signals

    def _calc_perp_size(self, price: float) -> float:
        if self._equity <= 0 or price <= 0:
            return 0.0
        notional = self._equity * 0.25 * self.perp_leverage
        return round(notional / price, 3)
