"""Strategy Lab #2 — Cross-Exchange Funding Rate Delta.

Market-neutral strategy that shorts the high-funding venue and longs the
low-funding venue for the same symbol. Captures the 8-hour funding delta
with zero net market exposure.

This is a SKELETON. Production requires:
    1. Multi-exchange connectivity (ccxt or native SDKs for Bybit / OKX / etc.)
    2. Cross-exchange margin / collateral tracking daemon
    3. Atomic dual-leg order placement with timeout rollback
    4. Independent settlement reconciliation

The lab implementation is CONSULTATIVE — it generates `SignalEvent`s for
the Binance leg only, with metadata describing the required remote leg.
An external executor layer (not in this repo) must handle the second leg.

NOT REGISTERED in register_default_strategies().
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog

from bot.core.constants import OrderSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class CrossExchangeFundingStrategy(BaseStrategy):
    """Cross-exchange funding rate arbitrage (lab prototype)."""

    name = "lab_cross_exchange_funding"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.min_delta_annual = params.get("min_delta_annual", 10.0)
        self.exit_delta_annual = params.get("exit_delta_annual", 3.0)
        self.max_hold_hours = params.get("max_hold_hours", 48)
        self.home_venue = params.get("home_venue", "binance")
        self.remote_venues = params.get("remote_venues", ["bybit", "okx"])

        self._funding_table: dict[str, dict[str, float]] = {}
        self._entry_times: dict[str, datetime] = {}
        self._entry_meta: dict[str, dict[str, Any]] = {}

    def inject_funding(self, venue: str, symbol: str, rate: float) -> None:
        """Lab hack: feed funding rates from external data client.

        `rate` is the per-interval (usually 8h) funding rate in decimal.
        In production, replace with a FundingSnapshotEvent type.
        """
        self._funding_table.setdefault(symbol, {})[venue] = rate

    def _annualize(self, rate_8h: float) -> float:
        return rate_8h * 3.0 * 365.0 * 100.0

    def _best_delta(self, symbol: str) -> tuple[str, str, float] | None:
        """Return (high_venue, low_venue, delta_annual_pct) or None."""
        table = self._funding_table.get(symbol, {})
        if self.home_venue not in table:
            return None
        venues = {v: self._annualize(r) for v, r in table.items()}
        high_v = max(venues, key=venues.get)
        low_v = min(venues, key=venues.get)
        delta = venues[high_v] - venues[low_v]
        return high_v, low_v, delta

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        self._record_bar(event)
        signals: list[SignalEvent] = []
        symbol = event.symbol
        pos = self._get_position(symbol)

        best = self._best_delta(symbol)
        if best is None:
            return signals
        high_v, low_v, delta = best

        if pos.is_open:
            should_exit = False
            reason = ""
            if delta < self.exit_delta_annual:
                should_exit, reason = True, f"delta normalized: {delta:.2f}%"
            entry_ts = self._entry_times.get(symbol)
            if entry_ts and event.timestamp - entry_ts >= timedelta(hours=self.max_hold_hours):
                should_exit, reason = True, f"max hold {self.max_hold_hours}h"

            if should_exit:
                side = OrderSide.BUY if pos.side.value == "short" else OrderSide.SELL
                meta = self._entry_meta.get(symbol, {})
                signals.append(self._create_signal(
                    symbol=symbol, side=side, quantity=pos.quantity,
                    timestamp=event.timestamp, reduce_only=True,
                    reason=f"[LAB-XFUND] exit: {reason}",
                    metadata={
                        "remote_leg_venue": meta.get("remote_leg_venue"),
                        "remote_leg_action": "close",
                        "delta_annual": delta,
                    },
                ))
                self._entry_times.pop(symbol, None)
                self._entry_meta.pop(symbol, None)
            return signals

        if delta < self.min_delta_annual:
            return signals

        qty = self._calc_size(event.close)
        if qty <= 0:
            return signals

        if high_v == self.home_venue:
            # Short home leg, long remote leg externally
            signals.append(self._create_signal(
                symbol=symbol, side=OrderSide.SELL, quantity=qty,
                timestamp=event.timestamp,
                reason=f"[LAB-XFUND] short home={high_v} vs long remote={low_v}, delta={delta:.2f}%",
                metadata={
                    "remote_leg_venue": low_v,
                    "remote_leg_side": "long",
                    "remote_leg_qty": qty,
                    "delta_annual": delta,
                },
            ))
            self._entry_times[symbol] = event.timestamp
            self._entry_meta[symbol] = {"remote_leg_venue": low_v, "side": "short_home"}
        elif low_v == self.home_venue:
            # Long home leg, short remote leg externally
            signals.append(self._create_signal(
                symbol=symbol, side=OrderSide.BUY, quantity=qty,
                timestamp=event.timestamp,
                reason=f"[LAB-XFUND] long home={low_v} vs short remote={high_v}, delta={delta:.2f}%",
                metadata={
                    "remote_leg_venue": high_v,
                    "remote_leg_side": "short",
                    "remote_leg_qty": qty,
                    "delta_annual": delta,
                },
            ))
            self._entry_times[symbol] = event.timestamp
            self._entry_meta[symbol] = {"remote_leg_venue": high_v, "side": "long_home"}

        return signals

    def _calc_size(self, price: float) -> float:
        if self._equity <= 0 or price <= 0:
            return 0.0
        notional = self._equity * 0.2 * self.leverage
        return round(notional / price, 4)
