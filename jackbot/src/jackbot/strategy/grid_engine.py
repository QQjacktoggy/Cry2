"""Grid Engine — manages limit-order grids for futures day-trading.

Emulates Binance Futures Grid Bot behaviour using standard limit orders:
  1. Calculates grid levels between upper and lower price bounds
  2. Places initial limit orders based on grid direction
  3. On each fill, places a counter-order on the opposing level
  4. Tracks matched profit for each completed buy→sell (or sell→buy) cycle
  5. Detects price breakouts beyond the grid range

Design reference: cry2/strategy/grid_futures.py grid-point calculation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from jackbot.core.constants import GridDirection, GridLevelState, OrderSide
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent
from jackbot.core.ownership import make_grid_client_order_id

logger = structlog.get_logger(__name__)


@dataclass
class GridLevel:
    """State of a single price level in the grid."""

    index: int
    price: float
    state: GridLevelState = GridLevelState.PENDING_BUY
    buy_order_id: str = ""
    sell_order_id: str = ""
    buy_client_order_ids: list[str] = field(default_factory=list)
    sell_client_order_ids: list[str] = field(default_factory=list)
    buy_fill_price: float = 0.0
    sell_fill_price: float = 0.0
    quantity: float = 0.0
    filled_quantity: float = 0.0      # actual filled/open quantity for this level
    matched_count: int = 0           # how many buy-sell cycles completed
    buy_signal_count: int = 0        # unique BUY client-order sequence for this level
    sell_signal_count: int = 0       # unique SELL client-order sequence for this level
    commission: float = 0.0          # total fee accumulated for this level



@dataclass
class GridInstance:
    """A complete grid configuration and its runtime state."""

    grid_id: str
    symbol: str
    direction: GridDirection
    upper_price: float
    lower_price: float
    grid_count: int
    leverage: int
    total_investment: float          # USDT allocated to this grid
    per_level_qty: float             # base-asset quantity per level
    levels: list[GridLevel] = field(default_factory=list)
    matched_profit: float = 0.0      # accumulated profit in USDT
    unrealized_pnl: float = 0.0      # current floating PnL
    total_matched: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed: bool = False
    close_reason: str = ""


class GridEngine:
    """Creates and manages futures grid instances.

    Each grid is an independent set of limit orders within a price range.
    The engine tracks fills and automatically places counter-orders.

    Leverage calculation for isolated margin:
      With 150 USDT capital, 10 grids, and 15x leverage:
      - Per grid notional = 150 / 10 * 15 = 225 USDT
      - For BTC at 95000: qty = 225 / 95000 ≈ 0.00237 BTC per level
    """

    def __init__(self) -> None:
        self._grids: dict[str, GridInstance] = {}  # grid_id → GridInstance

    @property
    def active_grids(self) -> list[GridInstance]:
        return [g for g in self._grids.values() if not g.closed]

    @property
    def all_grids(self) -> list[GridInstance]:
        return list(self._grids.values())

    def get_grid(self, grid_id: str) -> GridInstance | None:
        return self._grids.get(grid_id)

    def restore_grid(self, grid: GridInstance) -> None:
        """Hydrate a persisted grid back into the engine."""
        self._grids[grid.grid_id] = grid

    def replace_grids(self, grids: list[GridInstance]) -> None:
        self._grids = {grid.grid_id: grid for grid in grids}

    # ── Grid creation ─────────────────────────────────────────────────

    def create_grid(
        self,
        symbol: str,
        direction: GridDirection,
        upper_price: float,
        lower_price: float,
        grid_count: int,
        leverage: int,
        total_investment: float,
        current_price: float,
    ) -> tuple[GridInstance, list[GridSignalEvent]]:
        """Create a new grid and return the initial limit-order signals.

        Args:
            symbol: Trading pair (e.g. BTCUSDT)
            direction: LONG / SHORT / NEUTRAL
            upper_price: Grid upper boundary
            lower_price: Grid lower boundary
            grid_count: Number of grid intervals
            leverage: Isolated margin leverage (5~20x)
            total_investment: USDT allocated
            current_price: Current market price

        Returns:
            (grid_instance, initial_signals) — signals to be sent to the exchange
        """
        grid_id = f"grid_{symbol}_{uuid.uuid4().hex[:8]}"

        # Calculate grid level prices (arithmetic spacing)
        step = (upper_price - lower_price) / grid_count
        level_prices = [round(lower_price + i * step, 2) for i in range(grid_count + 1)]

        # Per-level quantity: (investment / grid_count) * leverage / price
        per_level_investment = total_investment / grid_count
        # Use mid-price for sizing
        mid_price = (upper_price + lower_price) / 2
        per_level_qty = round(per_level_investment * leverage / mid_price, 6)

        grid = GridInstance(
            grid_id=grid_id,
            symbol=symbol,
            direction=direction,
            upper_price=upper_price,
            lower_price=lower_price,
            grid_count=grid_count,
            leverage=leverage,
            total_investment=total_investment,
            per_level_qty=per_level_qty,
        )

        # Build levels
        for i, price in enumerate(level_prices):
            level = GridLevel(index=i, price=price, quantity=per_level_qty)
            grid.levels.append(level)

        # Generate initial orders based on direction
        now = datetime.now(UTC)
        signals = self._generate_initial_orders(grid, current_price, now)

        self._grids[grid_id] = grid

        logger.info(
            "grid_created",
            grid_id=grid_id,
            symbol=symbol,
            direction=direction.value,
            levels=len(level_prices),
            upper=upper_price,
            lower=lower_price,
            leverage=leverage,
            per_level_qty=per_level_qty,
            step=round(step, 2),
        )

        return grid, signals

    def _generate_initial_orders(
        self,
        grid: GridInstance,
        current_price: float,
        timestamp: datetime,
    ) -> list[GridSignalEvent]:
        """Generate initial limit orders for a new grid."""
        signals: list[GridSignalEvent] = []

        for level in grid.levels:
            if grid.direction == GridDirection.LONG:
                # Long grid: place BUY limits below current price
                if level.price < current_price:
                    level.state = GridLevelState.PENDING_BUY
                    signals.append(self._make_signal(
                        grid, level, OrderSide.BUY, level.price, timestamp,
                    ))
                else:
                    # Levels above current price start empty — they'll
                    # become SELL targets after lower BUYs fill
                    level.state = GridLevelState.PENDING_SELL

            elif grid.direction == GridDirection.SHORT:
                # Short grid: place SELL limits above current price
                if level.price > current_price:
                    level.state = GridLevelState.PENDING_SELL
                    signals.append(self._make_signal(
                        grid, level, OrderSide.SELL, level.price, timestamp,
                    ))
                else:
                    level.state = GridLevelState.PENDING_BUY

            else:
                # Neutral: BUY below, SELL above
                if level.price < current_price:
                    level.state = GridLevelState.PENDING_BUY
                    signals.append(self._make_signal(
                        grid, level, OrderSide.BUY, level.price, timestamp,
                    ))
                elif level.price > current_price:
                    level.state = GridLevelState.PENDING_SELL
                    signals.append(self._make_signal(
                        grid, level, OrderSide.SELL, level.price, timestamp,
                    ))

        return signals

    # ── Fill handling ─────────────────────────────────────────────────

    def on_fill(self, fill: FillEvent) -> tuple[list[GridSignalEvent], GridProfitEvent | None]:
        """Process a fill and return counter-orders + optional profit event.

        When a BUY fills at level i, place a SELL at level i+1.
        When a SELL fills at level i, place a BUY at level i-1.
        When both sides of a level complete → emit GridProfitEvent.
        """
        grid = self._grids.get(fill.grid_id)
        if grid is None or grid.closed:
            return [], None

        if fill.level_index < 0 or fill.level_index >= len(grid.levels):
            return [], None

        level = grid.levels[fill.level_index]
        signals: list[GridSignalEvent] = []
        profit_event: GridProfitEvent | None = None

        # Accumulate actual fee from this fill
        level.commission += fill.commission

        if fill.side == OrderSide.BUY.value:
            previous_qty = level.filled_quantity if level.state == GridLevelState.FILLED_BUY else 0.0
            total_qty = previous_qty + fill.quantity
            if total_qty > 0:
                level.buy_fill_price = (
                    (level.buy_fill_price * previous_qty) + (fill.price * fill.quantity)
                ) / total_qty
            level.buy_order_id = fill.order_id
            level.quantity = total_qty
            level.filled_quantity = total_qty
            level.state = GridLevelState.FILLED_BUY

            # Place SELL at the next higher level
            sell_index = fill.level_index + 1
            if sell_index < len(grid.levels):
                sell_level = grid.levels[sell_index]
                sell_level.state = GridLevelState.PENDING_SELL
                sell_level.quantity = fill.quantity
                sell_level.filled_quantity = 0.0
                signals.append(self._make_signal(
                    grid, sell_level, OrderSide.SELL, sell_level.price, fill.timestamp,
                ))

        elif fill.side == OrderSide.SELL.value:
            previous_qty = level.filled_quantity if level.state == GridLevelState.FILLED_SELL else 0.0
            total_qty = previous_qty + fill.quantity
            if total_qty > 0:
                level.sell_fill_price = (
                    (level.sell_fill_price * previous_qty) + (fill.price * fill.quantity)
                ) / total_qty
            level.sell_order_id = fill.order_id
            level.quantity = total_qty
            level.filled_quantity = total_qty
            level.state = GridLevelState.FILLED_SELL

            # Check for matched profit (buy at lower + sell at this level)
            buy_index = fill.level_index - 1
            if buy_index >= 0:
                buy_level = grid.levels[buy_index]
                if buy_level.state == GridLevelState.FILLED_BUY and buy_level.buy_fill_price > 0:
                    matched_qty = min(buy_level.filled_quantity, level.filled_quantity)
                    if matched_qty <= 0:
                        return signals, None
                    # Profit = (sell_price - buy_price) * quantity
                    profit = (level.sell_fill_price - buy_level.buy_fill_price) * matched_qty
                    grid.matched_profit += profit
                    grid.total_matched += 1
                    level.matched_count += 1
                    buy_level.matched_count += 1

                    # Total fee is buy fee + sell fee
                    total_fee = buy_level.commission + level.commission

                    profit_event = GridProfitEvent(
                        timestamp=fill.timestamp,
                        symbol=grid.symbol,
                        grid_id=grid.grid_id,
                        level_index=fill.level_index,
                        buy_price=buy_level.buy_fill_price,
                        sell_price=level.sell_fill_price,
                        quantity=matched_qty,
                        profit_usd=round(profit, 4),
                        commission=round(total_fee, 6),
                    )

                    logger.info(
                        "grid_profit_matched",
                        grid_id=grid.grid_id,
                        level=fill.level_index,
                        buy=buy_level.buy_fill_price,
                        sell=level.sell_fill_price,
                        profit=round(profit, 4),
                        fee=round(total_fee, 6),
                        total_matched=grid.total_matched,
                        total_profit=round(grid.matched_profit, 4),
                    )

                    # Reset level commissions for next cycle
                    buy_level.commission = 0.0
                    level.commission = 0.0

                    # Re-place BUY at the lower level for another cycle
                    buy_level.state = GridLevelState.PENDING_BUY
                    buy_level.quantity = matched_qty
                    buy_level.filled_quantity = 0.0
                    buy_level.buy_fill_price = 0.0
                    buy_level.buy_order_id = ""
                    signals.append(self._make_signal(
                        grid, buy_level, OrderSide.BUY, buy_level.price, fill.timestamp,
                    ))

        return signals, profit_event

    # ── Price monitoring ──────────────────────────────────────────────

    def update_unrealized_pnl(self, symbol: str, current_price: float) -> None:
        """Update unrealized PnL for all active grids of this symbol.

        For each filled level:
          - FILLED_BUY (holding long): pnl = (current - buy_price) * qty
          - FILLED_SELL (holding short): pnl = (sell_price - current) * qty
        """
        for grid in self.active_grids:
            if grid.symbol != symbol:
                continue
            pnl = 0.0
            for level in grid.levels:
                qty = level.filled_quantity or level.quantity
                if level.state == GridLevelState.FILLED_BUY and level.buy_fill_price > 0:
                    pnl += (current_price - level.buy_fill_price) * qty
                elif level.state == GridLevelState.FILLED_SELL and level.sell_fill_price > 0:
                    pnl += (level.sell_fill_price - current_price) * qty
            grid.unrealized_pnl = round(pnl, 4)

    def check_margin_rate(
        self,
        symbol: str,
        margin_rate_threshold: float = 1.10,
        maintenance_margin_rate: float = 0.005,
    ) -> list[str]:
        """Close grids whose remaining margin is dangerously close to liquidation.

        Fires before the unrealized-% stop-loss to handle gap-down scenarios
        where the 3% SL might not trigger in time.

        margin_rate = remaining_margin / maintenance_margin
        remaining_margin = total_investment + unrealized_pnl
        maintenance_margin = notional × maintenance_margin_rate
        """
        at_risk: list[str] = []
        for grid in self.active_grids:
            if grid.symbol != symbol:
                continue
            notional = grid.total_investment * grid.leverage
            maintenance_margin = notional * maintenance_margin_rate
            if maintenance_margin <= 0:
                continue
            remaining_margin = grid.total_investment + grid.unrealized_pnl
            margin_rate = remaining_margin / maintenance_margin
            if margin_rate < margin_rate_threshold:
                at_risk.append(grid.grid_id)
                logger.warning(
                    "margin_rate_critical",
                    grid_id=grid.grid_id,
                    remaining_margin=round(remaining_margin, 4),
                    maintenance_margin=round(maintenance_margin, 4),
                    margin_rate=round(margin_rate, 3),
                    threshold=margin_rate_threshold,
                )
        return at_risk

    def check_stop_loss(self, symbol: str, stop_loss_pct: float) -> list[str]:
        """Check if any grid's unrealized loss exceeds stop-loss threshold.

        Args:
            symbol: Trading pair
            stop_loss_pct: Max allowed loss as % of grid investment (e.g. 2.0 = 2%)

        Returns:
            List of grid_ids that hit stop-loss.
        """
        stopped: list[str] = []
        for grid in self.active_grids:
            if grid.symbol != symbol:
                continue
            if grid.total_investment <= 0:
                continue
            loss_pct = abs(grid.unrealized_pnl) / grid.total_investment * 100
            if grid.unrealized_pnl < 0 and loss_pct >= stop_loss_pct:
                stopped.append(grid.grid_id)
                logger.warning(
                    "grid_stop_loss_hit",
                    grid_id=grid.grid_id,
                    unrealized_pnl=grid.unrealized_pnl,
                    loss_pct=round(loss_pct, 2),
                    threshold=stop_loss_pct,
                )
        return stopped

    def check_breakout(self, symbol: str, current_price: float) -> list[str]:
        """Check if price has broken out of any active grid's range.

        Returns list of grid_ids that should be closed.
        """
        breakouts: list[str] = []
        for grid in self.active_grids:
            if grid.symbol != symbol:
                continue
            margin = (grid.upper_price - grid.lower_price) * 0.05  # 5% margin
            if current_price > grid.upper_price + margin:
                breakouts.append(grid.grid_id)
                logger.warning(
                    "grid_breakout_upper",
                    grid_id=grid.grid_id,
                    price=current_price,
                    upper=grid.upper_price,
                )
            elif current_price < grid.lower_price - margin:
                breakouts.append(grid.grid_id)
                logger.warning(
                    "grid_breakout_lower",
                    grid_id=grid.grid_id,
                    price=current_price,
                    lower=grid.lower_price,
                )
        return breakouts

    # ── Grid lifecycle ────────────────────────────────────────────────

    def close_grid(self, grid_id: str, reason: str = "manual") -> list[GridSignalEvent]:
        """Close a grid: cancel all pending orders and market-close positions.

        Returns cancel signals for all open orders.
        """
        grid = self._grids.get(grid_id)
        if grid is None or grid.closed:
            return []

        grid.closed = True
        grid.close_reason = reason
        signals: list[GridSignalEvent] = []
        now = datetime.now(UTC)

        for level in grid.levels:
            # Cancel any pending orders
            order_ids: list[str] = []
            if level.state == GridLevelState.PENDING_BUY:
                order_ids = level.buy_client_order_ids or ([level.buy_order_id] if level.buy_order_id else [])
            elif level.state == GridLevelState.PENDING_SELL:
                order_ids = level.sell_client_order_ids or ([level.sell_order_id] if level.sell_order_id else [])

            for order_id in order_ids:
                if order_id:
                    signals.append(GridSignalEvent(
                        timestamp=now,
                        symbol=grid.symbol,
                        side=OrderSide.BUY.value,
                        cancel_order_id=order_id,
                        grid_id=grid.grid_id,
                        level_index=level.index,
                        client_order_id=make_grid_client_order_id(
                            grid.grid_id, level.index, OrderSide.BUY.value, 99
                        ),
                    ))

            level.state = GridLevelState.CANCELLED

        logger.info(
            "grid_closed",
            grid_id=grid_id,
            reason=reason,
            matched_profit=round(grid.matched_profit, 4),
            total_matched=grid.total_matched,
        )

        return signals

    def get_total_profit(self) -> float:
        """Sum of matched profits across all grids (active + closed)."""
        return sum(g.matched_profit for g in self._grids.values())

    def get_status(self) -> list[dict[str, Any]]:
        """Status summary for each active grid."""
        result = []
        for g in self.active_grids:
            pending = sum(1 for level in g.levels
                          if level.state in (GridLevelState.PENDING_BUY, GridLevelState.PENDING_SELL))
            filled = sum(1 for level in g.levels
                         if level.state in (GridLevelState.FILLED_BUY, GridLevelState.FILLED_SELL))
            age_min = (datetime.now(UTC) - g.created_at).total_seconds() / 60

            level_details = [
                {
                    "price": level.price,
                    "state": level.state.value,
                    "buy_order": level.buy_order_id,
                    "sell_order": level.sell_order_id,
                }
                for level in g.levels
            ]

            result.append({
                "grid_id": g.grid_id,
                "symbol": g.symbol,
                "direction": g.direction.value,
                "levels": len(g.levels),
                "pending_orders": pending,
                "filled_levels": filled,
                "matched": g.total_matched,
                "realized_profit": round(g.matched_profit, 4),
                "unrealized_pnl": round(g.unrealized_pnl, 4),
                "net_pnl": round(g.matched_profit + g.unrealized_pnl, 4),
                "leverage": g.leverage,
                "range": f"{g.lower_price}~{g.upper_price}",
                "age_minutes": round(age_min, 1),
                "level_details": level_details,
            })
        return result

    # ── Helpers ───────────────────────────────────────────────────────

    def _make_signal(
        self,
        grid: GridInstance,
        level: GridLevel,
        side: OrderSide,
        price: float,
        timestamp: datetime,
    ) -> GridSignalEvent:
        if side == OrderSide.BUY:
            cycle = max(level.matched_count, level.buy_signal_count)
            level.buy_signal_count = cycle + 1
        else:
            cycle = max(level.matched_count, level.sell_signal_count)
            level.sell_signal_count = cycle + 1
        client_order_id = make_grid_client_order_id(
            grid.grid_id, level.index, side.value, cycle
        )
        if side == OrderSide.BUY:
            level.buy_client_order_ids.append(client_order_id)
        else:
            level.sell_client_order_ids.append(client_order_id)
        return GridSignalEvent(
            timestamp=timestamp,
            symbol=grid.symbol,
            side=side.value,
            order_type="LIMIT",
            price=price,
            quantity=level.quantity,
            grid_id=grid.grid_id,
            level_index=level.index,
            client_order_id=client_order_id,
            metadata={
                "leverage": grid.leverage,
                "upper": grid.upper_price,
                "lower": grid.lower_price,
            },
        )
