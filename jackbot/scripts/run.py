"""Jackbot_V1 — Main entry point.

Usage:
  python scripts/run.py                     # Connect to testnet and trade
  python scripts/run.py --dry-run           # Validate wiring without connecting
  python scripts/run.py --capital 200       # Override capital
  python scripts/run.py --status            # Print status and exit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add src/ to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import structlog
from dotenv import load_dotenv

from jackbot.config_utils import build_day_trader_params, load_merged_config
from jackbot.core.constants import GridLevelState
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent, MarketEvent
from jackbot.core.ownership import make_grid_client_order_id, parse_jackbot_client_order_id
from jackbot.exchange.client import BinanceClient
from jackbot.exchange.feed import KlineFeed
from jackbot.exchange.user_data import UserDataStream
from jackbot.notify.telegram import TelegramBot
from jackbot.portfolio.exchange_journal import ExchangeJournal
from jackbot.portfolio.portfolio import Portfolio, TradeRecord
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig

logger = structlog.get_logger("jackbot")


def load_config(path: str = "config/settings.yaml") -> dict:
    config_path = ROOT / path
    if not config_path.exists():
        logger.error("config_not_found", path=str(config_path))
        sys.exit(1)
    return load_merged_config(config_path)


class JackbotRunner:
    """Wires all components together and runs the trading loop."""

    def __init__(self, config: dict, dry_run: bool = False, capital_override: float = 0) -> None:
        self._cfg = config
        self._dry_run = dry_run

        # Capital
        trading = config.get("trading", {})
        self._capital = capital_override or trading.get("total_capital_usd", 150.0)
        symbols = trading.get("symbols", ["BTCUSDT", "ETHUSDT"])
        timeframe = trading.get("timeframe", "5m")

        # Event bus
        self._bus = EventBus()
        self._stop_event = asyncio.Event()

        # Exchange client
        exchange = config.get("exchange", {})
        testnet = exchange.get("mode", "testnet") == "testnet"
        self._client = BinanceClient(
            api_key=os.getenv(exchange.get("api_key_env", ""), ""),
            api_secret=os.getenv(exchange.get("api_secret_env", ""), ""),
            testnet=testnet,
            base_url=exchange.get("base_url", ""),
        )

        # Build DayTrader config from merged sections
        trader_params = build_day_trader_params(config, capital_override=self._capital)
        trader_params["symbols"] = symbols
        trader_params["timeframe"] = timeframe

        dt_config = DayTraderConfig.from_dict(trader_params)
        self._trader = DayTrader(config=dt_config, event_bus=self._bus)

        # Portfolio & session journal (resets every Docker launch)
        self._portfolio = Portfolio(initial_capital=self._capital)
        self._session_started_at = datetime.now(UTC)
        self._journal = ExchangeJournal()
        self._testnet = testnet
        self._safe_mode_reason = ""
        self._last_reconcile: dict = {
            "status": "not_run",
            "positions": [],
            "open_orders": [],
            "orphan_orders": [],
            "orphan_positions": [],
            "warnings": [],
        }
        self._last_repair_plan: dict = {
            "status": "not_run",
            "recoverable_actions": [],
            "blocking_issues": [],
            "warnings": [],
        }
        self._hydrate_runtime_state()

        # Telegram
        tg = config.get("telegram", {})
        self._telegram = TelegramBot(
            bot_token=os.getenv(tg.get("bot_token_env", ""), ""),
            chat_id=os.getenv(tg.get("chat_id_env", ""), ""),
            enabled=tg.get("enabled", False) and not dry_run,
        )

        # WebSocket feeds URLs
        ws_url = exchange.get("ws_url", "")

        # WebSocket feed
        self._feed = KlineFeed(symbols=symbols, timeframe=timeframe, testnet=testnet, ws_url=ws_url)
        self._feed.on_bar = self._on_bar

        # User data stream
        self._user_data = UserDataStream(api_client=self._client, testnet=testnet, ws_url=ws_url)
        self._user_data.on_fill = self._on_fill

        # Subscribe to profit events
        self._bus.subscribe("GridProfitEvent", self._on_profit)

        logger.info(
            "jackbot_initialized",
            capital=self._capital,
            symbols=symbols,
            timeframe=timeframe,
            dry_run=dry_run,
            testnet=testnet,
        )

    def _hydrate_runtime_state(self) -> None:
        """Restore persisted runtime state before live services start."""
        runtime_state = self._journal.load_runtime_state()
        restored_grids = self._journal.load_active_grids()

        if restored_grids:
            self._trader._engine.replace_grids(restored_grids)
            logger.info("runtime_grids_restored", count=len(restored_grids))

        if not runtime_state:
            return

        session_started_at = runtime_state.get("session_started_at", "")
        if session_started_at:
            try:
                self._session_started_at = datetime.fromisoformat(session_started_at)
            except ValueError:
                logger.warning("runtime_state_invalid_session_started_at", value=session_started_at)

        self._trader.restore_runtime_state(runtime_state.get("trader_state", {}))
        self._portfolio.restore_runtime_state(runtime_state.get("portfolio_state", {}))

        self._safe_mode_reason = str(runtime_state.get("safe_mode_reason", "") or "")
        if self._safe_mode_reason:
            self._trader.enter_safe_mode(self._safe_mode_reason)

        metadata = runtime_state.get("metadata", {})
        if isinstance(metadata, dict):
            self._last_reconcile = metadata.get("last_reconcile", self._last_reconcile)
            self._last_repair_plan = metadata.get("last_repair_plan", self._last_repair_plan)

        logger.info(
            "runtime_state_restored",
            session_started_at=self._session_started_at.isoformat(),
            safe_mode=bool(self._safe_mode_reason),
            active_grids=len(self._trader._engine.active_grids),
        )

    def _persist_runtime_state(self) -> None:
        """Persist the current runtime state for restart recovery."""
        for grid in self._trader._engine.all_grids:
            self._journal.save_grid_snapshot(grid)

        self._journal.save_runtime_state(
            session_started_at=self._session_started_at.isoformat(),
            safe_mode_reason=self._safe_mode_reason,
            trader_state=self._trader.snapshot_runtime_state(),
            portfolio_state=self._portfolio.snapshot_runtime_state(),
            metadata={
                "last_reconcile": self._last_reconcile,
                "last_repair_plan": self._last_repair_plan,
                "testnet": self._testnet,
                "symbols": list(self._trader._cfg.symbols),
                "timeframe": self._trader._cfg.timeframe,
                "strategy_variant": self._trader._cfg.strategy_variant,
            },
        )

    def _on_status_report(self) -> None:
        """Handle scheduled status report trigger."""
        logger.info("handling_scheduled_report")
        status = self.status()
        self._telegram.notify_status(status)

    def _on_bar(self, event: MarketEvent) -> None:
        """Handle each completed kline bar."""
        if self._safe_mode_reason:
            self._trader._last_prices[event.symbol] = event.close
            return

        signals = self._trader.on_bar(event)
        
        # Sync unrealized PnL to portfolio for accurate equity tracking
        self._portfolio.unrealized_pnl = self._trader.unrealized_pnl

        if self._dry_run:
            for s in signals:
                logger.info("dry_run_signal", side=s.side, price=s.price,
                            qty=s.quantity, grid_id=s.grid_id, level=s.level_index)
            self._persist_runtime_state()
            return

        # Execute signals on exchange
        for signal in signals:
            self._execute_signal(signal)
        self._persist_runtime_state()

    def _on_fill(self, fill: FillEvent) -> None:
        """Handle execution reports from the exchange."""
        fill = self._attach_grid_context(fill)
        self._journal.record_fill(fill)

        session_summary = self._journal.get_summary_since(self._session_started_at.isoformat())
        session_realized = float(session_summary.today_realized_pnl)
        session_commission = float(session_summary.today_commission)
        session_net_pnl = session_realized - session_commission

        if fill.realized_pnl == 0:
            self._telegram.notify_exchange_fill(
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                commission_asset=fill.commission_asset,
                realized_pnl=fill.realized_pnl,
                order_id=fill.order_id,
                trade_id=fill.trade_id,
                client_order_id=fill.client_order_id,
                grid_id=fill.grid_id,
                level_index=fill.level_index,
                daily_profit=session_net_pnl,
                daily_target=self._trader._cfg.daily_profit_target_usd,
            )

        if not fill.grid_id:
            logger.warning(
                "orphan_fill_recorded",
                order_id=fill.order_id,
                client_order_id=fill.client_order_id,
                symbol=fill.symbol,
            )
            self._enter_safe_mode("orphan_fill_without_grid_context")
            self._persist_runtime_state()
            return

        # Pass to trader
        signals = self._trader.on_fill(fill)
        
        # Execute any resulting counter-orders
        for s in signals:
            self._execute_signal(s)
        self._persist_runtime_state()

    def _attach_grid_context(self, fill: FillEvent) -> FillEvent:
        """Attach grid_id/level from clientOrderId or the in-memory grid map."""
        if fill.grid_id:
            return fill

        parsed = parse_jackbot_client_order_id(fill.client_order_id)
        if parsed is not None:
            grid_id, level_index = parsed
            return FillEvent(
                timestamp=fill.timestamp,
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                commission_asset=fill.commission_asset,
                realized_pnl=fill.realized_pnl,
                order_id=fill.order_id,
                trade_id=fill.trade_id,
                client_order_id=fill.client_order_id,
                grid_id=grid_id,
                level_index=level_index,
                source=fill.source,
                raw=fill.raw,
            )

        # Find grid_id if not present (exchange fills don't carry grid_id)
        for grid in self._trader._engine.active_grids:
            if grid.symbol != fill.symbol:
                continue
            for level in grid.levels:
                if level.buy_order_id == fill.order_id or level.sell_order_id == fill.order_id:
                    return FillEvent(
                        timestamp=fill.timestamp,
                        symbol=fill.symbol,
                        side=fill.side,
                        quantity=fill.quantity,
                        price=fill.price,
                        commission=fill.commission,
                        commission_asset=fill.commission_asset,
                        realized_pnl=fill.realized_pnl,
                        order_id=fill.order_id,
                        trade_id=fill.trade_id,
                        client_order_id=fill.client_order_id,
                        grid_id=grid.grid_id,
                        level_index=level.index,
                        source=fill.source,
                        raw=fill.raw,
                    )
        return fill

    def _execute_signal(self, signal: GridSignalEvent, *, allow_safe_mode_bypass: bool = False) -> bool:
        """Execute a grid signal on the exchange."""
        notional = self._client.order_notional(signal.symbol, signal.price, signal.quantity)
        signal_key = signal.client_order_id or f"cancel:{signal.cancel_order_id}"
        try:
            if (
                self._safe_mode_reason
                and not allow_safe_mode_bypass
                and not signal.cancel_order_id
                and not signal.reduce_only
            ):
                self._journal.save_order_signal(
                    client_order_id=signal_key,
                    grid_id=signal.grid_id,
                    level_index=signal.level_index,
                    symbol=signal.symbol,
                    side=signal.side,
                    order_type=signal.order_type,
                    price=signal.price,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                    cancel_order_id=signal.cancel_order_id,
                    notional=notional,
                    status="blocked_safe_mode",
                    error_message=self._safe_mode_reason,
                )
                logger.warning(
                    "signal_blocked_safe_mode",
                    reason=self._safe_mode_reason,
                    signal=signal.model_dump(),
                )
                self._persist_runtime_state()
                return False

            if signal.cancel_order_id:
                self._client.cancel_order(signal.symbol, signal.cancel_order_id)
                self._journal.save_order_signal(
                    client_order_id=signal_key,
                    grid_id=signal.grid_id,
                    level_index=signal.level_index,
                    symbol=signal.symbol,
                    side=signal.side,
                    order_type=signal.order_type,
                    price=signal.price,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                    cancel_order_id=signal.cancel_order_id,
                    notional=notional,
                    status="cancelled",
                    exchange_order_id=signal.cancel_order_id,
                )
                self._persist_runtime_state()
                return True

            min_notional = self._client.min_notional(signal.symbol)
            if min_notional > 0 and not signal.reduce_only and notional < min_notional:
                self._enter_safe_mode(
                    f"order_notional_below_min:{signal.symbol}:{notional:.4f}<{min_notional:.4f}"
                )
                self._journal.save_order_signal(
                    client_order_id=signal_key,
                    grid_id=signal.grid_id,
                    level_index=signal.level_index,
                    symbol=signal.symbol,
                    side=signal.side,
                    order_type=signal.order_type,
                    price=signal.price,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                    cancel_order_id=signal.cancel_order_id,
                    notional=notional,
                    status="blocked_min_notional",
                    error_message=f"min_notional={min_notional:.4f}",
                )
                self._telegram.notify_alert(
                    "Counter order blocked",
                    f"{signal.symbol} {signal.side} qty={signal.quantity} notional={notional:.4f} min={min_notional:.4f}",
                )
                logger.error(
                    "signal_blocked_min_notional",
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=signal.quantity,
                    price=signal.price,
                    notional=notional,
                    min_notional=min_notional,
                    signal=signal.model_dump(),
                )
                self._persist_runtime_state()
                return False

            # Set leverage before first order for each grid
            leverage = signal.metadata.get("leverage", 0)
            if leverage > 0:
                self._client.set_margin_type(signal.symbol, "ISOLATED")
                self._client.set_leverage(signal.symbol, leverage)

            if signal.order_type == "LIMIT":
                result = self._client.place_limit_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    price=signal.price,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                    client_order_id=signal.client_order_id,
                )
            else:
                result = self._client.place_market_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                )

            order_id = str(result.get("orderId", ""))
            self._journal.save_order_signal(
                client_order_id=signal_key,
                grid_id=signal.grid_id,
                level_index=signal.level_index,
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                price=signal.price,
                quantity=signal.quantity,
                reduce_only=signal.reduce_only,
                cancel_order_id=signal.cancel_order_id,
                notional=notional,
                status="placed",
                exchange_order_id=order_id,
            )

            # For limit orders, track the order ID on the grid level
            grid = self._trader._engine.get_grid(signal.grid_id)
            if grid and 0 <= signal.level_index < len(grid.levels):
                level = grid.levels[signal.level_index]
                if signal.side == "BUY":
                    level.buy_order_id = order_id
                else:
                    level.sell_order_id = order_id
            self._persist_runtime_state()
            return True

        except Exception as e:
            self._journal.save_order_signal(
                client_order_id=signal_key,
                grid_id=signal.grid_id,
                level_index=signal.level_index,
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                price=signal.price,
                quantity=signal.quantity,
                reduce_only=signal.reduce_only,
                cancel_order_id=signal.cancel_order_id,
                notional=notional,
                status="error",
                error_message=str(e),
            )
            logger.error("signal_execution_error", error=str(e), signal=signal.model_dump())
            self._persist_runtime_state()
            return False

    def _enter_safe_mode(self, reason: str) -> None:
        if self._safe_mode_reason:
            return
        self._safe_mode_reason = reason
        self._trader.enter_safe_mode(reason)
        logger.warning("runner_safe_mode_entered", reason=reason)
        self._persist_runtime_state()

    def _reconcile_exchange_state(self) -> dict:
        """Compare exchange state with in-memory strategy state."""
        report = {
            "status": "ok",
            "positions": [],
            "open_orders": [],
            "orphan_orders": [],
            "orphan_positions": [],
            "warnings": [],
        }
        active_grid_ids = {grid.grid_id for grid in self._trader._engine.active_grids}
        for symbol in self._trader._cfg.symbols:
            try:
                orders = self._client.get_open_orders(symbol)
                position = self._client.get_position(symbol)
                position_amt = float(position.get("positionAmt", 0) or 0)
            except Exception as e:
                logger.error("startup_exchange_state_check_failed", symbol=symbol, error=str(e))
                report["status"] = "error"
                report["warnings"].append(f"{symbol}: exchange state check failed: {e}")
                continue

            report["positions"].append(position)
            report["open_orders"].extend(orders)
            for order in orders:
                parsed = parse_jackbot_client_order_id(str(order.get("clientOrderId", "")))
                grid_id = parsed[0] if parsed else ""
                if not parsed or grid_id not in active_grid_ids:
                    report["orphan_orders"].append(order)
            if abs(position_amt) > 0 and not active_grid_ids:
                report["orphan_positions"].append(position)

        if report["orphan_orders"] or report["orphan_positions"]:
            report["status"] = "orphan_detected"
            report["warnings"].append(
                f"orphan_orders={len(report['orphan_orders'])}, "
                f"orphan_positions={len(report['orphan_positions'])}"
            )
        self._last_reconcile = report
        self._journal.append_reconcile_event(report)
        self._persist_runtime_state()
        logger.info(
            "exchange_reconcile_complete",
            status=report["status"],
            open_orders=len(report["open_orders"]),
            orphan_orders=len(report["orphan_orders"]),
            orphan_positions=len(report["orphan_positions"]),
        )
        return report

    def _build_repair_plan(self, refresh_exchange_state: bool = True) -> dict:
        """Inspect the current state and return a dry-run recovery plan."""
        report = self._reconcile_exchange_state() if refresh_exchange_state else self._last_reconcile
        open_orders = list(report.get("open_orders", []))
        positions = list(report.get("positions", []))
        active_grids = list(self._trader._engine.active_grids)
        open_order_ids = {str(order.get("orderId", "")) for order in open_orders if order.get("orderId")}
        parsed_open_orders: dict[tuple[str, int, str], dict] = {}
        for order in open_orders:
            client_order_id = str(order.get("clientOrderId", ""))
            parsed = parse_jackbot_client_order_id(client_order_id)
            if parsed is None:
                continue
            grid_id, level_index = parsed
            parsed_open_orders[(grid_id, level_index, str(order.get("side", "")).upper())] = order

        recoverable_actions: list[dict] = []
        blocking_issues: list[dict] = []
        warnings: list[str] = []
        planned_slots: set[tuple[str, int, str]] = set()

        for order in report.get("orphan_orders", []):
            blocking_issues.append(
                {
                    "kind": "orphan_order",
                    "symbol": str(order.get("symbol", "")),
                    "order_id": str(order.get("orderId", "")),
                    "client_order_id": str(order.get("clientOrderId", "")),
                    "detail": "exchange open order is not mapped to an active grid",
                }
            )

        for position in report.get("orphan_positions", []):
            blocking_issues.append(
                {
                    "kind": "orphan_position",
                    "symbol": str(position.get("symbol", "")),
                    "quantity": float(position.get("positionAmt", 0) or 0.0),
                    "entry_price": float(position.get("entryPrice", 0) or 0.0),
                    "detail": "exchange position exists without a managed active grid",
                }
            )

        for grid in active_grids:
            min_notional = self._client.min_notional(grid.symbol)
            for level in grid.levels:
                if level.state == GridLevelState.PENDING_BUY:
                    has_exchange_order = (
                        (level.buy_order_id and level.buy_order_id in open_order_ids)
                        or (grid.grid_id, level.index, "BUY") in parsed_open_orders
                    )
                    if not has_exchange_order and (grid.grid_id, level.index, "BUY") not in planned_slots:
                        planned_slots.add((grid.grid_id, level.index, "BUY"))
                        quantity = level.quantity or grid.per_level_qty
                        notional = self._client.order_notional(grid.symbol, level.price, quantity)
                        candidate = {
                            "kind": "place_missing_buy",
                            "symbol": grid.symbol,
                            "grid_id": grid.grid_id,
                            "level_index": level.index,
                            "side": "BUY",
                            "price": level.price,
                            "quantity": quantity,
                            "notional": notional,
                            "client_order_id": make_grid_client_order_id(
                                grid.grid_id, level.index, "BUY", level.matched_count
                            ),
                        }
                        if min_notional > 0 and notional < min_notional:
                            candidate["kind"] = "dust_pending_buy"
                            candidate["detail"] = f"pending BUY notional {notional:.4f} below min {min_notional:.4f}"
                            blocking_issues.append(candidate)
                        else:
                            recoverable_actions.append(candidate)

                if level.state == GridLevelState.PENDING_SELL:
                    has_exchange_order = (
                        (level.sell_order_id and level.sell_order_id in open_order_ids)
                        or (grid.grid_id, level.index, "SELL") in parsed_open_orders
                    )
                    if not has_exchange_order and (grid.grid_id, level.index, "SELL") not in planned_slots:
                        planned_slots.add((grid.grid_id, level.index, "SELL"))
                        quantity = level.quantity or grid.per_level_qty
                        notional = self._client.order_notional(grid.symbol, level.price, quantity)
                        candidate = {
                            "kind": "place_missing_sell",
                            "symbol": grid.symbol,
                            "grid_id": grid.grid_id,
                            "level_index": level.index,
                            "side": "SELL",
                            "price": level.price,
                            "quantity": quantity,
                            "notional": notional,
                            "client_order_id": make_grid_client_order_id(
                                grid.grid_id, level.index, "SELL", level.matched_count
                            ),
                        }
                        if min_notional > 0 and notional < min_notional:
                            candidate["kind"] = "dust_pending_sell"
                            candidate["detail"] = f"pending SELL notional {notional:.4f} below min {min_notional:.4f}"
                            blocking_issues.append(candidate)
                        else:
                            recoverable_actions.append(candidate)

                if level.state == GridLevelState.FILLED_BUY:
                    sell_index = level.index + 1
                    if sell_index >= len(grid.levels):
                        blocking_issues.append(
                            {
                                "kind": "filled_buy_without_exit_level",
                                "symbol": grid.symbol,
                                "grid_id": grid.grid_id,
                                "level_index": level.index,
                                "detail": "filled BUY is at the top grid level, no exit level exists above it",
                            }
                        )
                        continue
                    sell_level = grid.levels[sell_index]
                    has_counter_order = (
                        (sell_level.sell_order_id and sell_level.sell_order_id in open_order_ids)
                        or (grid.grid_id, sell_level.index, "SELL") in parsed_open_orders
                    )
                    if (
                        (sell_level.state != GridLevelState.PENDING_SELL or not has_counter_order)
                        and (grid.grid_id, sell_level.index, "SELL") not in planned_slots
                    ):
                        planned_slots.add((grid.grid_id, sell_level.index, "SELL"))
                        quantity = sell_level.quantity or level.filled_quantity or level.quantity or grid.per_level_qty
                        notional = self._client.order_notional(grid.symbol, sell_level.price, quantity)
                        candidate = {
                            "kind": "place_missing_counter_sell",
                            "symbol": grid.symbol,
                            "grid_id": grid.grid_id,
                            "source_level_index": level.index,
                            "level_index": sell_level.index,
                            "side": "SELL",
                            "price": sell_level.price,
                            "quantity": quantity,
                            "notional": notional,
                            "client_order_id": make_grid_client_order_id(
                                grid.grid_id, sell_level.index, "SELL", sell_level.matched_count
                            ),
                        }
                        if min_notional > 0 and notional < min_notional:
                            candidate["kind"] = "dust_counter_sell"
                            candidate["detail"] = f"counter SELL notional {notional:.4f} below min {min_notional:.4f}"
                            blocking_issues.append(candidate)
                        else:
                            recoverable_actions.append(candidate)

        plan_status = "ok"
        if blocking_issues:
            plan_status = "manual_review_required"
        elif recoverable_actions:
            plan_status = "recoverable"
        if self._safe_mode_reason:
            warnings.append(f"safe_mode={self._safe_mode_reason}")

        plan = {
            "status": plan_status,
            "safe_mode": bool(self._safe_mode_reason),
            "recoverable_actions": recoverable_actions,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "positions": positions,
            "open_orders": open_orders,
            "active_grids": [grid.grid_id for grid in active_grids],
        }
        self._last_repair_plan = plan
        self._persist_runtime_state()
        logger.info(
            "repair_plan_built",
            status=plan_status,
            recoverable=len(recoverable_actions),
            blocking=len(blocking_issues),
        )
        return plan

    def _execute_repair_plan(self, mode: str = "dryrun") -> dict:
        """Dry-run or execute recoverable repair actions."""
        normalized_mode = (mode or "dryrun").lower()
        if normalized_mode not in {"dryrun", "plan", "confirm", "apply"}:
            raise ValueError(f"unsupported repair mode: {mode}")

        plan = self._build_repair_plan(refresh_exchange_state=True)
        if normalized_mode in {"dryrun", "plan"}:
            return plan

        if not self._testnet:
            raise RuntimeError("repair confirm is limited to testnet for now")

        if plan.get("blocking_issues"):
            plan["execution"] = {
                "status": "blocked",
                "applied": 0,
                "failed": 0,
                "errors": ["blocking issues present; resolve manually before confirm"],
            }
            self._last_repair_plan = plan
            self._persist_runtime_state()
            return plan

        applied: list[dict] = []
        failed: list[dict] = []
        now = datetime.now(UTC)
        for action in plan.get("recoverable_actions", []):
            try:
                grid = self._trader._engine.get_grid(str(action.get("grid_id", "")))
                if grid is None:
                    raise RuntimeError("grid not found in memory")
                level_index = int(action.get("level_index", -1))
                if level_index < 0 or level_index >= len(grid.levels):
                    raise RuntimeError("level index out of range")
                level = grid.levels[level_index]
                side = str(action.get("side", "")).upper()
                quantity = float(action.get("quantity", 0.0) or 0.0)
                price = float(action.get("price", 0.0) or 0.0)
                if quantity <= 0 or price <= 0:
                    raise RuntimeError("invalid quantity or price")

                level.quantity = quantity
                if side == "BUY":
                    level.state = GridLevelState.PENDING_BUY
                    level.buy_order_id = ""
                elif side == "SELL":
                    level.state = GridLevelState.PENDING_SELL
                    level.sell_order_id = ""
                else:
                    raise RuntimeError(f"unsupported side: {side}")

                signal = GridSignalEvent(
                    timestamp=now,
                    symbol=str(action.get("symbol", "")),
                    side=side,
                    order_type="LIMIT",
                    price=price,
                    quantity=quantity,
                    grid_id=grid.grid_id,
                    level_index=level_index,
                    client_order_id=str(action.get("client_order_id", "")),
                    metadata={
                        "leverage": grid.leverage,
                        "upper": grid.upper_price,
                        "lower": grid.lower_price,
                        "repair": True,
                        "repair_kind": str(action.get("kind", "")),
                    },
                )
                success = self._execute_signal(signal, allow_safe_mode_bypass=True)
                if not success:
                    raise RuntimeError("repair signal execution failed")
                applied.append(action)
            except Exception as exc:
                failed.append(
                    {
                        **action,
                        "error": str(exc),
                    }
                )

        refreshed_plan = self._build_repair_plan(refresh_exchange_state=True)
        refreshed_plan["execution"] = {
            "status": "applied" if not failed else "partial_failure",
            "applied": len(applied),
            "failed": len(failed),
            "errors": [str(item.get("error", "")) for item in failed[:5]],
        }
        if failed:
            refreshed_plan["failed_actions"] = failed
        self._last_repair_plan = refreshed_plan
        self._persist_runtime_state()
        logger.info(
            "repair_plan_executed",
            applied=len(applied),
            failed=len(failed),
            remaining_recoverable=len(refreshed_plan.get("recoverable_actions", [])),
            remaining_blocking=len(refreshed_plan.get("blocking_issues", [])),
        )
        return refreshed_plan

    def _on_profit(self, event: GridProfitEvent) -> None:
        """Handle grid profit event."""
        trade = TradeRecord(
            timestamp=event.timestamp,
            symbol=event.symbol,
            grid_id=event.grid_id,
            buy_price=event.buy_price,
            sell_price=event.sell_price,
            quantity=event.quantity,
            profit_usd=event.profit_usd,
            commission=event.commission,
            leverage=0,
        )
        self._portfolio.record_trade(trade)
        session_summary = self._journal.get_summary_since(self._session_started_at.isoformat())
        self._persist_runtime_state()

        self._telegram.notify_grid_cycle(
            symbol=event.symbol,
            grid_id=event.grid_id,
            level_index=event.level_index,
            buy_price=event.buy_price,
            sell_price=event.sell_price,
            quantity=event.quantity,
            gross_profit=event.profit_usd,
            commission=event.commission,
            session_net=float(session_summary.today_realized_pnl) - float(session_summary.today_commission),
            target=self._trader._cfg.daily_profit_target_usd,
            equity=self._portfolio.total_equity,
        )

    async def run(self) -> None:
        """Main loop — warmup with historical klines, then stream live bars."""
        logger.info("jackbot_starting")

        if not self._dry_run:
            # Test connectivity
            try:
                latency = self._client.ping()
                balance = self._client.get_balance()
                logger.info("exchange_connected", latency_ms=latency, balance=balance)
            except Exception as e:
                logger.error("exchange_connection_failed", error=str(e))
                return

            # Load symbol precision info (qty/price decimal places)
            self._client.load_symbol_info(self._trader._cfg.symbols)

            reconcile = self._reconcile_exchange_state()
            if reconcile["status"] != "ok":
                self._enter_safe_mode("startup_exchange_orphan_state")
                self._telegram.notify_reconcile(reconcile, safe_mode=True)

            # Warmup: fetch historical klines (indicators only, no order placement)
            for symbol in self._trader._cfg.symbols:
                await self._warmup_symbol(symbol)
            self._trader.mark_warmup_complete()

            # Start WebSocket feed and Commander concurrently
            from jackbot.notify.commander import JackbotCommander
            commander = JackbotCommander(
                bot=self._telegram,
                trader=self._trader,
                portfolio=self._portfolio,
                client=self._client,
                stop_event=self._stop_event,
                journal=self._journal,
                started_at=self._session_started_at,
                status_provider=self.status,
                reconcile_callback=self._reconcile_exchange_state,
                safe_mode_callback=self._enter_safe_mode,
                repair_callback=self._build_repair_plan,
            )
            
            await asyncio.gather(
                self._feed.start(),
                self._user_data.start(),
                commander.run(),
                self._health_check_loop(),
            )
        else:
            logger.info("dry_run_mode — simulating with historical data")
            for symbol in self._trader._cfg.symbols:
                await self._warmup_symbol(symbol)
            logger.info("dry_run_complete", status=self._trader.get_status())

    async def _health_check_loop(self) -> None:
        """Periodic self-diagnostic task."""
        logger.info("health_check_loop_started")
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(300)  # 每 5 分鐘檢查一次
                self._reconcile_exchange_state()
                
                # 1. 檢查交易所連線
                latency = self._client.ping()
                if latency > 1000:
                    self._telegram.notify_alert("高延遲警報", f"交易所連線延遲過高: {latency}ms")
                
                # 2. 檢查下單引擎是否有 400 錯誤後的異常
                # 我們可以從最近的日誌或內部錯誤計數器檢查，這裡先以活躍網格掛單檢查為主
                for grid in self._trader._engine.active_grids:
                    orders = self._client.get_open_orders(grid.symbol)
                    if not orders:
                        self._telegram.notify_alert("網格掛單失蹤", f"{grid.symbol} 網格活躍中但在交易所找不到掛單")

                logger.debug("health_check_ok")
            except Exception as e:
                logger.error("health_check_error", error=str(e))

    async def _warmup_symbol(self, symbol: str) -> None:
        """Fetch historical klines and feed to DayTrader for warmup."""
        try:
            klines = self._client.get_klines(
                symbol=symbol,
                interval=self._trader._cfg.timeframe,
                limit=self._trader._cfg.warmup_bars + 10,
            )
            for k in klines:
                event = MarketEvent(
                    timestamp=datetime.fromtimestamp(k["timestamp"] / 1000, tz=UTC),
                    symbol=symbol,
                    timeframe=self._trader._cfg.timeframe,
                    open=k["open"],
                    high=k["high"],
                    low=k["low"],
                    close=k["close"],
                    volume=k["volume"],
                    source="warmup",
                )
                self._trader.on_bar(event)

            logger.info("warmup_complete", symbol=symbol, bars=len(klines))
        except Exception as e:
            logger.warning("warmup_failed", symbol=symbol, error=str(e))

    def status(self) -> dict:
        summary = self._portfolio.get_summary()
        session_summary = self._journal.get_summary_since(self._session_started_at.isoformat())
        session_fills = self._journal.fills_since(self._session_started_at.isoformat())
        negative_fills = [fill for fill in session_fills if float(fill.get("realized_pnl", 0.0) or 0.0) < 0]
        negative_fills = negative_fills[-5:]
        session_realized = float(session_summary.today_realized_pnl)
        session_commission = float(session_summary.today_commission)
        session_net = session_realized - session_commission
        exchange_snapshot = self._last_reconcile
        return {
            **self._trader.get_status(),
            **summary,
            "exchange_total_fills": session_summary.total_fills,
            "exchange_today_fills": session_summary.today_fills,
            "exchange_today_realized_pnl": session_realized,
            "exchange_today_commission": session_commission,
            "exchange_last_fill_at": session_summary.last_fill_at,
            "exchange_recent_fills": session_fills[-5:],
            "session_fills": session_summary.today_fills,
            "session_realized_pnl": session_realized,
            "session_commission": session_commission,
            "session_net_pnl": session_net,
            "session_negative_fill_count": len(negative_fills),
            "session_negative_fills": negative_fills,
            "session_last_fill_at": session_summary.last_fill_at,
            "daily_profit": session_net,
            "equity": summary["total_equity"],
            "total_fee": session_commission,
            "safe_mode_reason": self._safe_mode_reason,
            "exchange_reconcile": exchange_snapshot,
            "exchange_repair_plan": self._last_repair_plan,
            "exchange_positions": exchange_snapshot.get("positions", []),
            "exchange_open_orders": exchange_snapshot.get("open_orders", []),
            "exchange_orphan_orders": exchange_snapshot.get("orphan_orders", []),
            "exchange_orphan_positions": exchange_snapshot.get("orphan_positions", []),
        }

    def shutdown(self) -> None:
        close_signals = self._trader.close_all(reason="shutdown")
        for s in close_signals:
            self._execute_signal(s)
        self._persist_runtime_state()
        self._stop_event.set()
        self._client.close()
        self._telegram.send("🛑 <b>Jackbot_V1 已停止</b>")
        logger.info("jackbot_shutdown_complete")


def main():
    parser = argparse.ArgumentParser(description="Jackbot_V1 — Grid Day-Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Validate wiring only")
    parser.add_argument("--capital", type=float, default=0, help="Override capital (USDT)")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--config", default="config/settings.yaml", help="Config path")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    config = load_config(args.config)

    runner = JackbotRunner(
        config=config,
        dry_run=args.dry_run,
        capital_override=args.capital,
    )

    if args.status:
        import json
        print(json.dumps(runner.status(), indent=2, ensure_ascii=False))
        return

    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(runner_stop(runner)))
            
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception as e:
        logger.error("run_error", error=str(e))
    finally:
        runner.shutdown()


async def runner_stop(runner: JackbotRunner):
    logger.info("signal_received_shutting_down")
    runner.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
