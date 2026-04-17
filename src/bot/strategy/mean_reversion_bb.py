"""Strategy D: Mean Reversion (Bollinger + RSI).

Entry: Price at Bollinger lower + RSI < 30 → Long
       Price at Bollinger upper + RSI > 70 → Short
Only active when ATR > 70th percentile (high volatility).
Stop: 1.5% fixed. Take profit: Bollinger middle band.
"""

from __future__ import annotations

from typing import Any

import structlog

from bot.core.constants import OrderSide, PositionSide
from bot.core.events import MarketEvent, SignalEvent
from bot.core.types import Position
from bot.strategy.base import BaseStrategy
from bot.strategy.indicators.atr import calculate_atr
from bot.strategy.indicators.bollinger import calculate_bollinger, calculate_rsi

logger = structlog.get_logger(__name__)


class MeanReversionBBStrategy(BaseStrategy):
    """Mean reversion strategy using Bollinger Bands and RSI."""

    name = "mean_reversion_bb"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        params = params or {}
        self.bb_period = params.get("bb_period", 20)
        self.bb_std = params.get("bb_std", 2.0)
        self.rsi_period = params.get("rsi_period", 14)
        self.rsi_oversold = params.get("rsi_oversold", 30)
        self.rsi_overbought = params.get("rsi_overbought", 70)
        self.stop_loss_pct = params.get("stop_loss_pct", 1.5)
        self.take_profit_mode = params.get("take_profit", "bb_middle")

        vol_filter = params.get("volatility_filter", {})
        self.atr_percentile = vol_filter.get("atr_percentile", 70)

        # State
        self._atr_history: dict[str, list[float]] = {}

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Process bar for mean reversion signals."""
        self._record_bar(event, max_history=300)
        signals: list[SignalEvent] = []
        symbol = event.symbol

        closes = self._get_closes(symbol)
        highs = self._get_highs(symbol)
        lows = self._get_lows(symbol)

        if len(closes) < max(self.bb_period, self.rsi_period + 1, 100):
            return signals

        # Calculate indicators
        bb_upper, bb_middle, bb_lower = calculate_bollinger(closes, self.bb_period, self.bb_std)
        rsi = calculate_rsi(closes, self.rsi_period)
        atr = calculate_atr(highs, lows, closes, 14)

        # Volatility filter: only trade in high volatility
        if not self._passes_volatility_filter(symbol, atr):
            return signals

        pos = self._get_position(symbol)
        price = event.close

        if pos.is_open:
            # Manage existing position
            signals.extend(self._manage_position(symbol, pos, price, bb_middle, event.timestamp))
        else:
            # Look for entries
            if price <= bb_lower and rsi < self.rsi_oversold:
                # Oversold at lower band -> Long (mean reversion)
                quantity = self._calc_size(price)
                if quantity > 0:
                    signals.append(self._create_signal(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=quantity,
                        timestamp=event.timestamp,
                        reason=f"BB lower + RSI={rsi:.1f}",
                    ))

            elif price >= bb_upper and rsi > self.rsi_overbought:
                # Overbought at upper band -> Short (mean reversion)
                quantity = self._calc_size(price)
                if quantity > 0:
                    signals.append(self._create_signal(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=quantity,
                        timestamp=event.timestamp,
                        reason=f"BB upper + RSI={rsi:.1f}",
                    ))

        return signals

    def _manage_position(
        self,
        symbol: str,
        pos: Position,
        price: float,
        bb_middle: float,
        timestamp: Any,
    ) -> list[SignalEvent]:
        """Manage position - check stop loss and take profit."""
        signals: list[SignalEvent] = []

        if pos.side == PositionSide.LONG:
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100

            if pnl_pct <= -self.stop_loss_pct:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason=f"Stop loss hit: {pnl_pct:.2f}%",
                ))
            elif price >= bb_middle:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason="Take profit: BB middle",
                ))

        elif pos.side == PositionSide.SHORT:
            pnl_pct = (pos.entry_price - price) / pos.entry_price * 100

            if pnl_pct <= -self.stop_loss_pct:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason=f"Stop loss hit: {pnl_pct:.2f}%",
                ))
            elif price <= bb_middle:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=pos.quantity,
                    timestamp=timestamp,
                    reduce_only=True,
                    reason="Take profit: BB middle",
                ))

        return signals

    def _passes_volatility_filter(self, symbol: str, current_atr: float) -> bool:
        """Check if current ATR is above historical percentile."""
        if symbol not in self._atr_history:
            self._atr_history[symbol] = []

        self._atr_history[symbol].append(current_atr)

        # Keep last 100 ATR values
        if len(self._atr_history[symbol]) > 100:
            self._atr_history[symbol] = self._atr_history[symbol][-100:]

        if len(self._atr_history[symbol]) < 20:
            return False

        sorted_atrs = sorted(self._atr_history[symbol])
        threshold_idx = int(len(sorted_atrs) * self.atr_percentile / 100)
        threshold = sorted_atrs[min(threshold_idx, len(sorted_atrs) - 1)]

        return current_atr >= threshold

    def _calc_size(self, price: float) -> float:
        """Simplified position sizing."""
        if price <= 0:
            return 0.0
        return (100.0 / price) * self.leverage

    def warmup_bars(self) -> int:
        return max(self.bb_period, self.rsi_period + 1, 100) + 5
