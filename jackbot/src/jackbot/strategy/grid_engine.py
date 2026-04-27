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

logger = structlog.get_logger(__name__)


@dataclass
class GridLevel:
    """State of a single price level in the grid."""

    index: int
    price: float
    state: GridLevelState = GridLevelState.PENDING_BUY
    buy_order_id: str = ""
    sell_order_id: str = ""
    buy_fill_price: float = 0.0
    sell_fill_price: float = 0.0
    quantity: float = 0.0
    matched_count: int = 0           # how many buy→sell cycles completed

    # t1-partial-tp-trailing — used only when grid.partial_tp_enabled
    partial_filled_qty: float = 0.0  # qty already taken via partial TP
    trailing_active: bool = False    # set after partial TP fires
    high_water_mark: float = 0.0     # best price since trailing started


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
    partial_tp_profit: float = 0.0   # USDT taken via partial TP (subset of total realised)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed: bool = False
    close_reason: str = ""

    # t1-partial-tp-trailing (per-grid configuration; OFF by default)
    partial_tp_enabled: bool = False
    partial_tp_pct: float = 1.0          # floating-pct threshold to trigger
    partial_tp_close_pct: float = 0.5    # fraction of position to close
    trailing_atr_mult: float = 0.5       # trailing exit dist = ATR × this

    # t1-maker-only-close (per-grid; OFF by default)
    maker_only_close: bool = False
    maker_close_tick_size: float = 0.1   # offset applied to current price


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

    def get_grid(self, grid_id: str) -> GridInstance | None:
        return self._grids.get(grid_id)

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
        partial_tp_enabled: bool = False,
        partial_tp_pct: float = 1.0,
        partial_tp_close_pct: float = 0.5,
        trailing_atr_mult: float = 0.5,
        maker_only_close: bool = False,
        maker_close_tick_size: float = 0.1,
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
            partial_tp_enabled=partial_tp_enabled,
            partial_tp_pct=partial_tp_pct,
            partial_tp_close_pct=partial_tp_close_pct,
            trailing_atr_mult=trailing_atr_mult,
            maker_only_close=maker_only_close,
            maker_close_tick_size=maker_close_tick_size,
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

        if fill.side == OrderSide.BUY.value:
            level.buy_fill_price = fill.price
            level.buy_order_id = fill.order_id
            level.state = GridLevelState.FILLED_BUY

            # Place SELL at the next higher level
            sell_index = fill.level_index + 1
            if sell_index < len(grid.levels):
                sell_level = grid.levels[sell_index]
                sell_level.state = GridLevelState.PENDING_SELL
                sell_level.quantity = level.quantity
                signals.append(self._make_signal(
                    grid, sell_level, OrderSide.SELL, sell_level.price, fill.timestamp,
                ))

        elif fill.side == OrderSide.SELL.value:
            level.sell_fill_price = fill.price
            level.sell_order_id = fill.order_id
            level.state = GridLevelState.FILLED_SELL

            # Check for matched profit (buy at lower + sell at this level)
            buy_index = fill.level_index - 1
            if buy_index >= 0:
                buy_level = grid.levels[buy_index]
                if buy_level.state == GridLevelState.FILLED_BUY and buy_level.buy_fill_price > 0:
                    # Profit = (sell_price - buy_price) * quantity
                    profit = (level.sell_fill_price - buy_level.buy_fill_price) * level.quantity
                    grid.matched_profit += profit
                    grid.total_matched += 1
                    level.matched_count += 1
                    buy_level.matched_count += 1

                    profit_event = GridProfitEvent(
                        timestamp=fill.timestamp,
                        symbol=grid.symbol,
                        grid_id=grid.grid_id,
                        level_index=fill.level_index,
                        buy_price=buy_level.buy_fill_price,
                        sell_price=level.sell_fill_price,
                        quantity=level.quantity,
                        profit_usd=round(profit, 4),
                    )

                    logger.info(
                        "grid_profit_matched",
                        grid_id=grid.grid_id,
                        level=fill.level_index,
                        buy=buy_level.buy_fill_price,
                        sell=level.sell_fill_price,
                        profit=round(profit, 4),
                        total_matched=grid.total_matched,
                        total_profit=round(grid.matched_profit, 4),
                    )

                    # Re-place BUY at the lower level for another cycle
                    buy_level.state = GridLevelState.PENDING_BUY
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
                if level.state == GridLevelState.FILLED_BUY and level.buy_fill_price > 0:
                    pnl += (current_price - level.buy_fill_price) * level.quantity
                elif level.state == GridLevelState.FILLED_SELL and level.sell_fill_price > 0:
                    pnl += (level.sell_fill_price - current_price) * level.quantity
            grid.unrealized_pnl = round(pnl, 4)

    def check_partial_tp_and_trailing(
        self,
        symbol: str,
        current_price: float,
        atr: float,
    ) -> tuple[list[GridSignalEvent], list[str]]:
        """Run partial-TP / trailing logic for grids that have it enabled.

        Returns:
            (partial_close_signals, trailing_exit_grid_ids)

        Partial TP fires when a filled level's floating PnL ≥ partial_tp_pct;
        emits a market reduce-only order for partial_tp_close_pct of the held
        quantity, reduces level.quantity in place, and arms trailing.

        Trailing exit fires when, after partial TP, price retraces by
        atr × trailing_atr_mult from the high-water-mark; the caller should
        close the entire grid with reason="trailing_exit".
        """
        signals: list[GridSignalEvent] = []
        trailing_grids: list[str] = []
        now = datetime.now(UTC)

        for grid in self.active_grids:
            if grid.symbol != symbol or not grid.partial_tp_enabled:
                continue

            tp_threshold = grid.partial_tp_pct / 100.0
            trail_dist = atr * grid.trailing_atr_mult

            for level in grid.levels:
                # Long-side filled level: holding base asset, profit on upside
                if level.state == GridLevelState.FILLED_BUY and level.buy_fill_price > 0:
                    entry = level.buy_fill_price
                    floating_pct = (current_price - entry) / entry

                    if not level.trailing_active and floating_pct >= tp_threshold:
                        close_qty = round(level.quantity * grid.partial_tp_close_pct, 6)
                        if close_qty > 0:
                            signals.append(GridSignalEvent(
                                timestamp=now,
                                symbol=grid.symbol,
                                side=OrderSide.SELL.value,
                                order_type="MARKET",
                                price=0.0,
                                quantity=close_qty,
                                grid_id=grid.grid_id,
                                level_index=level.index,
                                reduce_only=True,
                                metadata={"reason": "partial_tp"},
                            ))
                            partial_profit = (current_price - entry) * close_qty
                            grid.partial_tp_profit += partial_profit
                            grid.matched_profit += partial_profit
                            level.partial_filled_qty += close_qty
                            level.quantity = round(level.quantity - close_qty, 6)
                            level.trailing_active = True
                            level.high_water_mark = current_price
                            logger.info(
                                "partial_tp_triggered",
                                grid_id=grid.grid_id,
                                level=level.index,
                                entry=entry,
                                price=current_price,
                                close_qty=close_qty,
                                profit=round(partial_profit, 4),
                            )
                    elif level.trailing_active:
                        if current_price > level.high_water_mark:
                            level.high_water_mark = current_price
                        if current_price <= level.high_water_mark - trail_dist:
                            trailing_grids.append(grid.grid_id)
                            logger.warning(
                                "trailing_exit_long",
                                grid_id=grid.grid_id,
                                level=level.index,
                                hwm=level.high_water_mark,
                                price=current_price,
                                trail_dist=round(trail_dist, 4),
                            )
                            break  # whole grid will be closed; no need to keep scanning

                # Short-side filled level: holding short, profit on downside
                elif level.state == GridLevelState.FILLED_SELL and level.sell_fill_price > 0:
                    entry = level.sell_fill_price
                    floating_pct = (entry - current_price) / entry

                    if not level.trailing_active and floating_pct >= tp_threshold:
                        close_qty = round(level.quantity * grid.partial_tp_close_pct, 6)
                        if close_qty > 0:
                            signals.append(GridSignalEvent(
                                timestamp=now,
                                symbol=grid.symbol,
                                side=OrderSide.BUY.value,
                                order_type="MARKET",
                                price=0.0,
                                quantity=close_qty,
                                grid_id=grid.grid_id,
                                level_index=level.index,
                                reduce_only=True,
                                metadata={"reason": "partial_tp"},
                            ))
                            partial_profit = (entry - current_price) * close_qty
                            grid.partial_tp_profit += partial_profit
                            grid.matched_profit += partial_profit
                            level.partial_filled_qty += close_qty
                            level.quantity = round(level.quantity - close_qty, 6)
                            level.trailing_active = True
                            level.high_water_mark = current_price
                            logger.info(
                                "partial_tp_triggered_short",
                                grid_id=grid.grid_id,
                                level=level.index,
                                entry=entry,
                                price=current_price,
                                close_qty=close_qty,
                                profit=round(partial_profit, 4),
                            )
                    elif level.trailing_active:
                        if current_price < level.high_water_mark:
                            level.high_water_mark = current_price
                        if current_price >= level.high_water_mark + trail_dist:
                            trailing_grids.append(grid.grid_id)
                            logger.warning(
                                "trailing_exit_short",
                                grid_id=grid.grid_id,
                                level=level.index,
                                hwm=level.high_water_mark,
                                price=current_price,
                                trail_dist=round(trail_dist, 4),
                            )
                            break

        return signals, trailing_grids

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

    def close_grid(
        self,
        grid_id: str,
        reason: str = "manual",
        current_price: float = 0.0,
    ) -> list[GridSignalEvent]:
        """Close a grid: cancel pending orders, optionally close open positions.

        With maker_only_close OFF (legacy default): only cancels pending orders.
        With maker_only_close ON: also emits post-only LIMIT reduce-only orders
        to close any held positions, priced one tick on the passive side of the
        current price. The live runner is responsible for cancelling and
        retrying as MARKET if the LIMIT fails to fill within its timeout window
        (TODO: hook 5s timer in scripts/run.py).

        Returns the list of signals (cancels + optional close LIMITs).
        """
        grid = self._grids.get(grid_id)
        if grid is None or grid.closed:
            return []

        grid.closed = True
        grid.close_reason = reason
        signals: list[GridSignalEvent] = []
        now = datetime.now(UTC)
        tick = grid.maker_close_tick_size

        for level in grid.levels:
            # Cancel any pending orders
            order_id = ""
            if level.state == GridLevelState.PENDING_BUY and level.buy_order_id:
                order_id = level.buy_order_id
            elif level.state == GridLevelState.PENDING_SELL and level.sell_order_id:
                order_id = level.sell_order_id

            if order_id:
                signals.append(GridSignalEvent(
                    timestamp=now,
                    symbol=grid.symbol,
                    side=OrderSide.BUY.value,
                    cancel_order_id=order_id,
                    grid_id=grid.grid_id,
                    level_index=level.index,
                ))

            # t1-maker-only-close: emit reduce-only LIMIT to close held positions
            if grid.maker_only_close and level.quantity > 0:
                if level.state == GridLevelState.FILLED_BUY and level.buy_fill_price > 0:
                    close_price = round(
                        current_price + tick if current_price > 0 else level.price + tick,
                        2,
                    )
                    signals.append(GridSignalEvent(
                        timestamp=now,
                        symbol=grid.symbol,
                        side=OrderSide.SELL.value,
                        order_type="LIMIT",
                        price=close_price,
                        quantity=level.quantity,
                        grid_id=grid.grid_id,
                        level_index=level.index,
                        reduce_only=True,
                        metadata={"close_intent": "maker_only", "fallback_after_s": 5},
                    ))
                elif level.state == GridLevelState.FILLED_SELL and level.sell_fill_price > 0:
                    close_price = round(
                        current_price - tick if current_price > 0 else level.price - tick,
                        2,
                    )
                    signals.append(GridSignalEvent(
                        timestamp=now,
                        symbol=grid.symbol,
                        side=OrderSide.BUY.value,
                        order_type="LIMIT",
                        price=close_price,
                        quantity=level.quantity,
                        grid_id=grid.grid_id,
                        level_index=level.index,
                        reduce_only=True,
                        metadata={"close_intent": "maker_only", "fallback_after_s": 5},
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
            pending = sum(1 for l in g.levels
                          if l.state in (GridLevelState.PENDING_BUY, GridLevelState.PENDING_SELL))
            filled = sum(1 for l in g.levels
                         if l.state in (GridLevelState.FILLED_BUY, GridLevelState.FILLED_SELL))
            age_min = (datetime.now(UTC) - g.created_at).total_seconds() / 60
            
            level_details = [
                {
                    "price": l.price,
                    "state": l.state.value,
                    "buy_order": l.buy_order_id,
                    "sell_order": l.sell_order_id,
                }
                for l in g.levels
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
        return GridSignalEvent(
            timestamp=timestamp,
            symbol=grid.symbol,
            side=side.value,
            order_type="LIMIT",
            price=price,
            quantity=level.quantity,
            grid_id=grid.grid_id,
            level_index=level.index,
            metadata={"leverage": grid.leverage},
        )
