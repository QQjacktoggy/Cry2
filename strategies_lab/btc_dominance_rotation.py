"""Strategy Lab #5 — BTC Dominance Rotation.

Regime overlay driven by BTC dominance (BTC market cap / total crypto market cap).
When BTC.D decisively falls while OTHERS.D rises, rotate long into top alts.
When BTC.D rises, short alts as a defensive hedge.

This is a SKELETON. Production requires:
    1. A market-cap data client (CoinGecko / DefiLlama / CMC) — not present in V7.2
    2. Cross-symbol portfolio rebalancing logic
    3. Daily-frequency scheduler (signal is low-frequency)

The lab skeleton uses inject_dominance() to receive BTC.D / OTHERS.D values
from an external data layer. This keeps the strategy testable without adding
a new data source dependency to the main project.

NOT REGISTERED in register_default_strategies().
"""

from __future__ import annotations

from collections import deque
from typing import Any

import structlog

from bot.core.constants import OrderSide, PositionSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class BtcDominanceRotationStrategy(BaseStrategy):
    """BTC dominance rotation with alt basket exposure (lab prototype)."""

    name = "lab_btc_dominance_rotation"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.alt_basket: list[str] = params.get(
            "alt_basket", ["ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
        )
        self.btc_d_ma_period = params.get("btc_d_ma_period", 30)
        self.btc_d_down_pct = params.get("btc_d_down_pct", 0.05)
        self.btc_d_up_pct = params.get("btc_d_up_pct", 0.05)
        self.per_alt_alloc = params.get("per_alt_alloc", 0.03)
        self.defensive_short = params.get("defensive_short", False)

        self._btc_d_history: deque[float] = deque(maxlen=self.btc_d_ma_period * 3)
        self._others_d_history: deque[float] = deque(maxlen=self.btc_d_ma_period * 3)
        self._last_regime: str | None = None

    def inject_dominance(self, btc_d: float, others_d: float) -> None:
        """Lab hack: feed BTC.D / OTHERS.D (both in 0-100 percent)."""
        self._btc_d_history.append(btc_d)
        self._others_d_history.append(others_d)

    def _classify_regime(self) -> str | None:
        if len(self._btc_d_history) < self.btc_d_ma_period:
            return None
        btc_d_ma = sum(list(self._btc_d_history)[-self.btc_d_ma_period:]) / self.btc_d_ma_period
        others_d_ma = (
            sum(list(self._others_d_history)[-self.btc_d_ma_period:]) / self.btc_d_ma_period
        )
        latest_btc = self._btc_d_history[-1]
        latest_oth = self._others_d_history[-1]

        if (
            latest_btc < btc_d_ma * (1 - self.btc_d_down_pct)
            and latest_oth > others_d_ma * (1 + self.btc_d_down_pct)
        ):
            return "alt_season"
        if latest_btc > btc_d_ma * (1 + self.btc_d_up_pct):
            return "btc_flight"
        return "neutral"

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        self._record_bar(event)
        signals: list[SignalEvent] = []
        if event.symbol not in self.alt_basket:
            return signals

        regime = self._classify_regime()
        if regime is None:
            return signals

        pos = self._get_position(event.symbol)
        qty = self._calc_alt_size(event.close)
        if qty <= 0:
            return signals

        # Regime transition → rebalance
        if regime == "alt_season" and pos.side != PositionSide.LONG:
            if pos.side == PositionSide.SHORT and pos.quantity > 0:
                signals.append(self._create_signal(
                    symbol=event.symbol, side=OrderSide.BUY, quantity=pos.quantity,
                    timestamp=event.timestamp, reduce_only=True,
                    reason=f"[LAB-BTCD] exit short on alt_season regime",
                ))
            signals.append(self._create_signal(
                symbol=event.symbol, side=OrderSide.BUY, quantity=qty,
                timestamp=event.timestamp,
                reason=f"[LAB-BTCD] alt_season long regime={regime}",
                metadata={"regime": regime, "btc_d": self._btc_d_history[-1]},
            ))
        elif regime == "btc_flight" and pos.side != PositionSide.FLAT:
            exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            signals.append(self._create_signal(
                symbol=event.symbol, side=exit_side, quantity=pos.quantity,
                timestamp=event.timestamp, reduce_only=True,
                reason=f"[LAB-BTCD] exit on btc_flight regime",
            ))
            if self.defensive_short:
                signals.append(self._create_signal(
                    symbol=event.symbol, side=OrderSide.SELL, quantity=qty,
                    timestamp=event.timestamp,
                    reason=f"[LAB-BTCD] defensive short on btc_flight",
                    metadata={"regime": regime},
                ))

        self._last_regime = regime
        return signals

    def _calc_alt_size(self, price: float) -> float:
        if self._equity <= 0 or price <= 0:
            return 0.0
        notional = self._equity * self.per_alt_alloc * self.leverage
        return round(notional / price, 4)
