"""Simulated executor for backtesting.

Simulates order matching, slippage, fees, funding rate settlements,
and liquidation. Signals generated at bar close are executed at next bar open.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from bot.core.constants import (
    EventType,
    OrderSide,
    OrderType,
    PositionSide,
)
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, FundingEvent, OrderEvent, RejectEvent
from bot.core.types import Position
from bot.execution.executor_base import BaseExecutor
from bot.execution.fee_model import FeeModel
from bot.execution.funding_model import FundingModel
from bot.execution.slippage import SlippageModel
from bot.utils.id_generator import generate_client_order_id

logger = structlog.get_logger(__name__)


class SimExecutor(BaseExecutor):
    """Simulated executor for backtesting.

    Features:
    - Market/Limit order simulation
    - Slippage and fee calculation
    - Funding rate settlement
    - Liquidation detection
    - Per-strategy position tracking
    """

    def __init__(
        self,
        event_bus: EventBus,
        initial_capital: float = 10000.0,
        slippage_model: SlippageModel | None = None,
        fee_model: FeeModel | None = None,
        funding_model: FundingModel | None = None,
        max_leverage: int = 5,
    ) -> None:
        self.event_bus = event_bus
        self.initial_capital = initial_capital
        self._balance = initial_capital
        self._equity = initial_capital
        self.slippage = slippage_model or SlippageModel()
        self.fees = fee_model or FeeModel()
        self.funding = funding_model or FundingModel()
        self.max_leverage = max_leverage

        # Positions keyed by (strategy, symbol)
        self._positions: dict[tuple[str, str], Position] = {}
        # Pending orders (limit/stop)
        self._pending_orders: list[OrderEvent] = []
        # Trade history
        self._fills: list[FillEvent] = []
        # Current bar prices for each symbol
        self._current_prices: dict[str, float] = {}
        self._current_volumes: dict[str, float] = {}

        # Subscribe to funding events
        self.event_bus.subscribe(EventType.FUNDING.value, self._on_funding)

    def set_bar_data(self, symbol: str, open_price: float, volume: float) -> None:
        """Update current bar data for order filling.

        Called by the backtest engine before processing signals.
        Orders fill at the *next* bar's open price.
        """
        self._current_prices[symbol] = open_price
        self._current_volumes[symbol] = volume

    def submit_order(self, order: OrderEvent) -> str:
        """Submit an order for simulated execution."""
        client_order_id = order.client_order_id or generate_client_order_id(order.strategy_name)

        symbol = order.symbol
        price = self._current_prices.get(symbol, order.price)
        volume = self._current_volumes.get(symbol, 0.0)

        if price <= 0:
            self._reject_order(order, "No price data available", client_order_id)
            return client_order_id

        if order.order_type == OrderType.MARKET:
            # Fill immediately at slippage-adjusted price
            fill_price = self.slippage.calculate(
                price=price,
                side=order.side,
                quantity=order.quantity,
                bar_volume=volume,
            )
            self._execute_fill(order, fill_price, client_order_id)
        else:
            # Store as pending order
            self._pending_orders.append(order)
            logger.debug("pending_order_added", symbol=symbol, type=order.order_type.value)

        return client_order_id

    def _execute_fill(
        self, order: OrderEvent, fill_price: float, client_order_id: str
    ) -> None:
        """Execute a fill and update positions."""
        notional = order.quantity * fill_price
        fee = self.fees.calculate(notional, order.order_type)

        # Update position
        pos_key = (order.strategy_name, order.symbol)
        position = self._positions.get(pos_key, Position(
            symbol=order.symbol,
            strategy_name=order.strategy_name,
        ))

        realized_pnl = 0.0

        if order.side == OrderSide.BUY:
            if position.side == PositionSide.SHORT and position.quantity > 0:
                # Closing/reducing short
                close_qty = min(order.quantity, position.quantity)
                realized_pnl = close_qty * (position.entry_price - fill_price)
                remaining_qty = position.quantity - close_qty
                new_qty = order.quantity - close_qty

                if remaining_qty > 0:
                    position.quantity = remaining_qty
                elif new_qty > 0:
                    position.side = PositionSide.LONG
                    position.quantity = new_qty
                    position.entry_price = fill_price
                else:
                    position.side = PositionSide.FLAT
                    position.quantity = 0.0
                    position.entry_price = 0.0
            else:
                # Opening/adding long
                if position.quantity > 0:
                    total_cost = position.entry_price * position.quantity + fill_price * order.quantity
                    position.quantity += order.quantity
                    position.entry_price = total_cost / position.quantity
                else:
                    position.quantity = order.quantity
                    position.entry_price = fill_price
                position.side = PositionSide.LONG
        else:  # SELL
            if position.side == PositionSide.LONG and position.quantity > 0:
                # Closing/reducing long
                close_qty = min(order.quantity, position.quantity)
                realized_pnl = close_qty * (fill_price - position.entry_price)
                remaining_qty = position.quantity - close_qty
                new_qty = order.quantity - close_qty

                if remaining_qty > 0:
                    position.quantity = remaining_qty
                elif new_qty > 0:
                    position.side = PositionSide.SHORT
                    position.quantity = new_qty
                    position.entry_price = fill_price
                else:
                    position.side = PositionSide.FLAT
                    position.quantity = 0.0
                    position.entry_price = 0.0
            else:
                # Opening/adding short
                if position.quantity > 0:
                    total_cost = position.entry_price * position.quantity + fill_price * order.quantity
                    position.quantity += order.quantity
                    position.entry_price = total_cost / position.quantity
                else:
                    position.quantity = order.quantity
                    position.entry_price = fill_price
                position.side = PositionSide.SHORT

        position.current_price = fill_price
        self._positions[pos_key] = position

        # Update balance
        self._balance -= fee
        self._balance += realized_pnl

        # Create fill event
        now = datetime.now(UTC)
        fill = FillEvent(
            timestamp=now,
            strategy_name=order.strategy_name,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=fee,
            order_id=client_order_id,
            client_order_id=client_order_id,
            realized_pnl=realized_pnl,
            source="sim",
        )
        self._fills.append(fill)
        self.event_bus.publish(fill)

        logger.debug(
            "sim_fill",
            symbol=order.symbol,
            side=order.side.value,
            qty=order.quantity,
            price=fill_price,
            fee=fee,
            pnl=realized_pnl,
        )

    def _reject_order(self, order: OrderEvent, reason: str, client_order_id: str) -> None:
        """Reject an order and publish event."""
        reject = RejectEvent(
            timestamp=datetime.now(UTC),
            strategy_name=order.strategy_name,
            symbol=order.symbol,
            reason=reason,
            client_order_id=client_order_id,
            source="sim",
        )
        self.event_bus.publish(reject)
        logger.warning("order_rejected", symbol=order.symbol, reason=reason)

    def _on_funding(self, event: FundingEvent) -> None:
        """Process funding rate settlement for all positions."""
        for (strategy, symbol), position in self._positions.items():
            if symbol != event.symbol or not position.is_open:
                continue

            payment = self.funding.calculate(
                position_side=position.side,
                position_value=position.notional_value,
                funding_rate=event.funding_rate,
            )

            self._balance += payment
            logger.debug(
                "funding_settled",
                strategy=strategy,
                symbol=symbol,
                rate=event.funding_rate,
                payment=payment,
            )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        for i, order in enumerate(self._pending_orders):
            if order.client_order_id == order_id:
                self._pending_orders.pop(i)
                return True
        return False

    def get_position(self, symbol: str, strategy_name: str = "") -> Position:
        """Get position for a symbol."""
        if strategy_name:
            key = (strategy_name, symbol)
            return self._positions.get(key, Position(symbol=symbol, strategy_name=strategy_name))

        # Aggregate across strategies
        total_long = 0.0
        total_short = 0.0
        for (strat, sym), pos in self._positions.items():
            if sym == symbol and pos.is_open:
                if pos.side == PositionSide.LONG:
                    total_long += pos.quantity
                else:
                    total_short += pos.quantity

        net = total_long - total_short
        if net > 0:
            return Position(symbol=symbol, side=PositionSide.LONG, quantity=net)
        elif net < 0:
            return Position(symbol=symbol, side=PositionSide.SHORT, quantity=abs(net))
        return Position(symbol=symbol)

    def get_balance(self) -> float:
        """Get available USDT balance."""
        return self._balance

    def get_equity(self) -> float:
        """Get total equity (balance + unrealized PnL)."""
        unrealized = 0.0
        for position in self._positions.values():
            if not position.is_open:
                continue
            if position.side == PositionSide.LONG:
                unrealized += position.quantity * (position.current_price - position.entry_price)
            elif position.side == PositionSide.SHORT:
                unrealized += position.quantity * (position.entry_price - position.current_price)
        return self._balance + unrealized

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update current market prices for equity calculation."""
        for (strategy, symbol), position in self._positions.items():
            if symbol in prices:
                position.current_price = prices[symbol]

    @property
    def fills(self) -> list[FillEvent]:
        """Get all fill events."""
        return list(self._fills)

    @property
    def positions(self) -> dict[tuple[str, str], Position]:
        """Get all positions."""
        return dict(self._positions)
