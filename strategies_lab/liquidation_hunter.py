"""Strategy Lab #4 — Liquidation Cascade Hunter.

Contrarian entry after detecting a likely liquidation cascade. Looks for:
    - Extreme bar range (>3%)
    - Volume spike (>2x rolling avg)
    - Sequential candle direction alignment

Production version should additionally use Binance's OI and long-short ratio
APIs, which are not part of V7.2's MarketEvent. The lab skeleton uses only
bar-level proxies so that it can run against existing data_dir.

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


class LiquidationHunterStrategy(BaseStrategy):
    """Contrarian entry after liquidation-like bar proxy signals."""

    name = "lab_liquidation_hunter"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.allocation = params.get("allocation", 0.03)
        self.bar_range_pct_threshold = params.get("bar_range_pct_threshold", 0.03)
        self.volume_spike_mult = params.get("volume_spike_mult", 2.0)
        self.vol_lookback = params.get("vol_lookback", 20)
        self.consecutive_bars = params.get("consecutive_bars", 2)
        self.exit_bars = params.get("exit_bars", 4)
        self.stop_atr_mult = params.get("stop_atr_mult", 1.5)
        self.atr_period = params.get("atr_period", 14)

        self._volumes: dict[str, deque[float]] = {}
        self._directions: dict[str, deque[int]] = {}
        self._trs: dict[str, deque[float]] = {}
        self._bars_since_entry: dict[str, int] = {}
        self._entry_price: dict[str, float] = {}
        self._prev_close: dict[str, float] = {}

    def warmup_bars(self) -> int:
        return max(self.vol_lookback, self.atr_period) + 5

    def _update_state(self, event: MarketEvent) -> None:
        sym = event.symbol
        if sym not in self._volumes:
            self._volumes[sym] = deque(maxlen=self.vol_lookback)
            self._directions[sym] = deque(maxlen=self.consecutive_bars)
            self._trs[sym] = deque(maxlen=self.atr_period)
        self._volumes[sym].append(event.volume)
        direction = 1 if event.close > event.open else (-1 if event.close < event.open else 0)
        self._directions[sym].append(direction)
        prev_close = self._prev_close.get(sym, event.close)
        tr = max(
            event.high - event.low,
            abs(event.high - prev_close),
            abs(event.low - prev_close),
        )
        self._trs[sym].append(tr)
        self._prev_close[sym] = event.close

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        self._record_bar(event)
        self._update_state(event)
        signals: list[SignalEvent] = []
        sym = event.symbol
        pos = self._get_position(sym)

        if pos.is_open:
            self._bars_since_entry[sym] = self._bars_since_entry.get(sym, 0) + 1
            entry_price = self._entry_price.get(sym, event.close)
            atr = sum(self._trs[sym]) / len(self._trs[sym]) if self._trs.get(sym) else 0.0
            stop_distance = atr * self.stop_atr_mult

            hit_stop = False
            if pos.side == PositionSide.LONG and event.low <= entry_price - stop_distance:
                hit_stop = True
            elif pos.side == PositionSide.SHORT and event.high >= entry_price + stop_distance:
                hit_stop = True

            exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            if hit_stop:
                signals.append(self._create_signal(
                    symbol=sym, side=exit_side, quantity=pos.quantity,
                    timestamp=event.timestamp, reduce_only=True,
                    reason="[LAB-LIQ] ATR stop",
                ))
                self._bars_since_entry.pop(sym, None)
                self._entry_price.pop(sym, None)
            elif self._bars_since_entry[sym] >= self.exit_bars:
                signals.append(self._create_signal(
                    symbol=sym, side=exit_side, quantity=pos.quantity,
                    timestamp=event.timestamp, reduce_only=True,
                    reason=f"[LAB-LIQ] max bars {self.exit_bars}",
                ))
                self._bars_since_entry.pop(sym, None)
                self._entry_price.pop(sym, None)
            return signals

        # Entry — require sufficient history
        if len(self._volumes[sym]) < self.vol_lookback:
            return signals

        bar_range_pct = (event.high - event.low) / event.open if event.open > 0 else 0
        avg_vol = sum(self._volumes[sym]) / len(self._volumes[sym])
        vol_spike = event.volume > avg_vol * self.volume_spike_mult
        dirs = list(self._directions[sym])
        aligned_down = len(dirs) == self.consecutive_bars and all(d == -1 for d in dirs)
        aligned_up = len(dirs) == self.consecutive_bars and all(d == 1 for d in dirs)

        is_cascade_down = bar_range_pct > self.bar_range_pct_threshold and vol_spike and aligned_down
        is_cascade_up = bar_range_pct > self.bar_range_pct_threshold and vol_spike and aligned_up

        qty = self._calc_size(event.close)
        if qty <= 0:
            return signals

        if is_cascade_down:
            signals.append(self._create_signal(
                symbol=sym, side=OrderSide.BUY, quantity=qty,
                timestamp=event.timestamp,
                reason=f"[LAB-LIQ] contra-long range={bar_range_pct:.3f} vol_spike={event.volume/avg_vol:.1f}x",
                metadata={"bar_range_pct": bar_range_pct},
            ))
            self._bars_since_entry[sym] = 0
            self._entry_price[sym] = event.close
        elif is_cascade_up:
            signals.append(self._create_signal(
                symbol=sym, side=OrderSide.SELL, quantity=qty,
                timestamp=event.timestamp,
                reason=f"[LAB-LIQ] contra-short range={bar_range_pct:.3f} vol_spike={event.volume/avg_vol:.1f}x",
                metadata={"bar_range_pct": bar_range_pct},
            ))
            self._bars_since_entry[sym] = 0
            self._entry_price[sym] = event.close

        return signals

    def _calc_size(self, price: float) -> float:
        if self._equity <= 0 or price <= 0:
            return 0.0
        notional = self._equity * self.allocation * self.leverage
        return round(notional / price, 4)
