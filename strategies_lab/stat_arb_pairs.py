"""Strategy Lab #3 — Statistical Pairs Trading with Kalman Filter.

Market-neutral pairs strategy trading the spread between two cointegrated
symbols (e.g., ETHUSDT vs BTCUSDT). Uses a simple Kalman-filter analogue
(recursive OLS with forgetting factor) to track time-varying hedge ratio.

Logic:
    spread_t = log(P_A_t) - beta_t * log(P_B_t)
    z_t     = (spread_t - spread_mean) / spread_std
    if z > upper_z and flat:  short A, long B * beta
    if z < -upper_z and flat: long A, short B * beta
    if |z| < exit_z:          close both legs

This is a SKELETON — a lightweight recursive update replaces pykalman to
avoid adding a dependency at the lab stage. Production should plug in the
real Kalman filter from pykalman / filterpy.

NOT REGISTERED in register_default_strategies().
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

import structlog

from bot.core.constants import OrderSide, PositionSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class StatArbPairsStrategy(BaseStrategy):
    """Cointegration pairs trading with adaptive hedge ratio."""

    name = "lab_stat_arb_pairs"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.symbol_a = params.get("symbol_a", "ETHUSDT")
        self.symbol_b = params.get("symbol_b", "BTCUSDT")
        self.allocation = params.get("allocation", 0.08)
        self.entry_z = params.get("entry_z", 2.0)
        self.exit_z = params.get("exit_z", 0.5)
        self.stop_z = params.get("stop_z", 4.0)
        self.spread_window = params.get("spread_window", 120)
        self.kalman_decay = params.get("kalman_decay", 0.995)

        self._prices_a: dict[int, float] = {}
        self._prices_b: dict[int, float] = {}
        self._spread_history: deque[float] = deque(maxlen=self.spread_window)
        self._beta: float = 1.0
        self._beta_init = False
        self._side: str | None = None

    def _update_beta(self, pa: float, pb: float) -> None:
        """Recursive OLS-like update on log prices with forgetting factor."""
        la, lb = math.log(pa), math.log(pb)
        if not self._beta_init:
            self._beta = la / lb if lb != 0 else 1.0
            self._beta_init = True
            return
        residual = la - self._beta * lb
        self._beta = self._beta * self.kalman_decay + (residual / lb if lb != 0 else 0) * (1 - self.kalman_decay)

    def _spread_stats(self) -> tuple[float, float]:
        if len(self._spread_history) < 10:
            return 0.0, 1.0
        mean = sum(self._spread_history) / len(self._spread_history)
        var = sum((x - mean) ** 2 for x in self._spread_history) / len(self._spread_history)
        std = math.sqrt(var) if var > 0 else 1.0
        return mean, std

    def _calc_leg_sizes(self, price_a: float, price_b: float) -> tuple[float, float]:
        if self._equity <= 0 or price_a <= 0 or price_b <= 0:
            return 0.0, 0.0

        hedge_ratio = max(abs(self._beta), 0.1)
        total_notional = self._equity * self.allocation * self.leverage
        if total_notional <= 0:
            return 0.0, 0.0

        notional_a = total_notional / (1.0 + hedge_ratio)
        notional_b = total_notional - notional_a
        qty_a = round(notional_a / price_a, 4)
        qty_b = round(notional_b / price_b, 4)
        return qty_a, qty_b

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        self._record_bar(event)
        signals: list[SignalEvent] = []

        ts_bucket = int(event.timestamp.timestamp()) // 3600  # hourly align
        if event.symbol == self.symbol_a:
            self._prices_a[ts_bucket] = event.close
        elif event.symbol == self.symbol_b:
            self._prices_b[ts_bucket] = event.close
        else:
            return []

        if ts_bucket not in self._prices_a or ts_bucket not in self._prices_b:
            return []

        pa, pb = self._prices_a[ts_bucket], self._prices_b[ts_bucket]
        if pa <= 0 or pb <= 0:
            return []

        self._update_beta(pa, pb)
        spread = math.log(pa) - self._beta * math.log(pb)
        self._spread_history.append(spread)

        mean, std = self._spread_stats()
        z = (spread - mean) / std if std > 0 else 0.0

        pos_a = self._get_position(self.symbol_a)
        pos_b = self._get_position(self.symbol_b)

        # Exit logic
        if self._side == "short_spread" and (z < self.exit_z or z > self.stop_z):
            if pos_a.is_open:
                signals.append(self._create_signal(
                    symbol=self.symbol_a, side=OrderSide.BUY,
                    quantity=pos_a.quantity, timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"[LAB-PAIRS] exit short_spread z={z:.2f}",
                ))
            if pos_b.is_open:
                signals.append(self._create_signal(
                    symbol=self.symbol_b, side=OrderSide.SELL,
                    quantity=pos_b.quantity, timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"[LAB-PAIRS] exit hedge long_b z={z:.2f}",
                ))
            self._side = None
        elif self._side == "long_spread" and (z > -self.exit_z or z < -self.stop_z):
            if pos_a.is_open:
                signals.append(self._create_signal(
                    symbol=self.symbol_a, side=OrderSide.SELL,
                    quantity=pos_a.quantity, timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"[LAB-PAIRS] exit long_spread z={z:.2f}",
                ))
            if pos_b.is_open:
                signals.append(self._create_signal(
                    symbol=self.symbol_b, side=OrderSide.BUY,
                    quantity=pos_b.quantity, timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"[LAB-PAIRS] exit hedge short_b z={z:.2f}",
                ))
            self._side = None

        # Entry logic
        if self._side is None and pos_a.side == PositionSide.FLAT and pos_b.side == PositionSide.FLAT:
            qty_a, qty_b = self._calc_leg_sizes(pa, pb)
            if qty_a <= 0 or qty_b <= 0:
                return signals
            if z > self.entry_z:
                signals.append(self._create_signal(
                    symbol=self.symbol_a, side=OrderSide.SELL,
                    quantity=qty_a, timestamp=event.timestamp,
                    reason=f"[LAB-PAIRS] enter short_spread z={z:.2f} beta={self._beta:.4f}",
                    metadata={"pair_leg_b": self.symbol_b, "hedge_ratio": self._beta, "z": z},
                ))
                signals.append(self._create_signal(
                    symbol=self.symbol_b, side=OrderSide.BUY,
                    quantity=qty_b, timestamp=event.timestamp,
                    reason=f"[LAB-PAIRS] enter hedge_long_b z={z:.2f} beta={self._beta:.4f}",
                    metadata={"pair_leg_a": self.symbol_a, "hedge_ratio": self._beta, "z": z},
                ))
                self._side = "short_spread"
            elif z < -self.entry_z:
                signals.append(self._create_signal(
                    symbol=self.symbol_a, side=OrderSide.BUY,
                    quantity=qty_a, timestamp=event.timestamp,
                    reason=f"[LAB-PAIRS] enter long_spread z={z:.2f} beta={self._beta:.4f}",
                    metadata={"pair_leg_b": self.symbol_b, "hedge_ratio": self._beta, "z": z},
                ))
                signals.append(self._create_signal(
                    symbol=self.symbol_b, side=OrderSide.SELL,
                    quantity=qty_b, timestamp=event.timestamp,
                    reason=f"[LAB-PAIRS] enter hedge_short_b z={z:.2f} beta={self._beta:.4f}",
                    metadata={"pair_leg_a": self.symbol_a, "hedge_ratio": self._beta, "z": z},
                ))
                self._side = "long_spread"

        return signals
