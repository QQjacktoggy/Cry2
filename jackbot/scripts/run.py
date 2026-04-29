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
import yaml
from dotenv import load_dotenv

from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent, MarketEvent
from jackbot.exchange.client import BinanceClient
from jackbot.exchange.feed import KlineFeed
from jackbot.exchange.user_data import UserDataStream
from jackbot.notify.telegram import TelegramBot
from jackbot.portfolio.portfolio import Portfolio, TradeRecord
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig

logger = structlog.get_logger("jackbot")


def load_config(path: str = "config/settings.yaml") -> dict:
    config_path = ROOT / path
    if not config_path.exists():
        logger.error("config_not_found", path=str(config_path))
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
        trader_params = {}
        trader_params.update(trading)
        trader_params.update(config.get("grid", {}))
        trader_params.update(config.get("targets", {}))
        trader_params.update(config.get("conservative", {}))
        trader_params.update(config.get("risk", {}))
        trader_params.update(config.get("market_assessor", {}))
        trader_params.update(config.get("leverage", {
            "max_leverage": 10,
            "min_leverage": 5,
        }))
        trader_params["total_capital_usd"] = self._capital
        trader_params["symbols"] = symbols
        trader_params["timeframe"] = timeframe

        dt_config = DayTraderConfig.from_dict(trader_params)
        self._trader = DayTrader(config=dt_config, event_bus=self._bus)

        # Portfolio
        self._portfolio = Portfolio(initial_capital=self._capital)

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
        self._bus.subscribe("status_report", self._on_status_report)

        logger.info(
            "jackbot_initialized",
            capital=self._capital,
            symbols=symbols,
            timeframe=timeframe,
            dry_run=dry_run,
            testnet=testnet,
        )

    def _on_status_report(self) -> None:
        """Handle scheduled status report trigger."""
        logger.info("handling_scheduled_report")
        status = self.status()
        self._telegram.notify_status(status)

    def _on_bar(self, event: MarketEvent) -> None:
        """Handle each completed kline bar."""
        signals = self._trader.on_bar(event)
        
        # Sync unrealized PnL to portfolio for accurate equity tracking
        self._portfolio.unrealized_pnl = self._trader.unrealized_pnl

        if self._dry_run:
            for s in signals:
                logger.info("dry_run_signal", side=s.side, price=s.price,
                            qty=s.quantity, grid_id=s.grid_id, level=s.level_index)
            return

        # Execute signals on exchange
        for signal in signals:
            self._execute_signal(signal)

    def _on_fill(self, fill: FillEvent) -> None:
        """Handle execution reports from the exchange."""
        # Find grid_id if not present (exchange fills don't carry grid_id)
        if not fill.grid_id:
            # Search active grids for this symbol and order_id
            for grid in self._trader._engine.active_grids:
                if grid.symbol != fill.symbol:
                    continue
                for level in grid.levels:
                    if level.buy_order_id == fill.order_id or level.sell_order_id == fill.order_id:
                        # Re-construct fill with grid context
                        fill = FillEvent(
                            timestamp=fill.timestamp,
                            symbol=fill.symbol,
                            side=fill.side,
                            quantity=fill.quantity,
                            price=fill.price,
                            commission=fill.commission,
                            realized_pnl=fill.realized_pnl,
                            order_id=fill.order_id,
                            client_order_id=fill.client_order_id,
                            grid_id=grid.grid_id,
                            level_index=level.index,
                            source=fill.source,
                        )
                        break
                if fill.grid_id:
                    break

        if not fill.grid_id:
            logger.debug("fill_ignored_no_grid_match", order_id=fill.order_id, symbol=fill.symbol)
            return

        # Pass to trader
        signals = self._trader.on_fill(fill)
        
        # Execute any resulting counter-orders
        for s in signals:
            self._execute_signal(s)

    def _execute_signal(self, signal: GridSignalEvent) -> None:
        """Execute a grid signal on the exchange."""
        try:
            if signal.cancel_order_id:
                self._client.cancel_order(signal.symbol, signal.cancel_order_id)
                return

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
                )
            else:
                result = self._client.place_market_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=signal.quantity,
                    reduce_only=signal.reduce_only,
                )

            order_id = str(result.get("orderId", ""))

            # For limit orders, track the order ID on the grid level
            grid = self._trader._engine.get_grid(signal.grid_id)
            if grid and 0 <= signal.level_index < len(grid.levels):
                level = grid.levels[signal.level_index]
                if signal.side == "BUY":
                    level.buy_order_id = order_id
                else:
                    level.sell_order_id = order_id

        except Exception as e:
            logger.error("signal_execution_error", error=str(e), signal=signal.model_dump())

    def _has_existing_exchange_state(self) -> bool:
        """Return True when restarting would risk orphaned or duplicate grids."""
        found_state = False

        for symbol in self._trader._cfg.symbols:
            try:
                orders = self._client.get_open_orders(symbol)
                position = self._client.get_position(symbol)
                position_amt = float(position.get("positionAmt", 0) or 0)
            except Exception as e:
                logger.error("startup_exchange_state_check_failed", symbol=symbol, error=str(e))
                return True

            if orders or abs(position_amt) > 0:
                found_state = True
                logger.error(
                    "startup_existing_exchange_state",
                    symbol=symbol,
                    open_orders=len(orders),
                    position_amt=position_amt,
                    action="refuse_to_start",
                )

        return found_state

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

        self._telegram.notify_grid_profit(
            profit=event.profit_usd,
            total=self._trader.daily_profit,
            target=self._trader._cfg.daily_profit_target_usd,
            equity=self._portfolio.total_equity,
            fee=event.commission,
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

            if self._has_existing_exchange_state():
                self._telegram.send(
                    "⚠️ <b>Jackbot 啟動中止</b>\n"
                    "偵測到交易所已有未成交單或持倉，為避免重啟後疊單，請先清理狀態。"
                )
                return

            # Warmup: fetch historical klines (indicators only, no order placement)
            for symbol in self._trader._cfg.symbols:
                await self._warmup_symbol(symbol)
            self._trader.mark_warmup_complete()

            # Notify start
            self._telegram.send(
                f"🚀 <b>Jackbot_V1 啟動</b>\n"
                f"資金: ${self._capital}\n"
                f"幣種: {', '.join(self._trader._cfg.symbols)}\n"
                f"日標: ${self._trader._cfg.daily_profit_target_usd}"
            )

            # Start WebSocket feed and Commander concurrently
            from jackbot.notify.commander import JackbotCommander
            commander = JackbotCommander(
                bot=self._telegram,
                trader=self._trader,
                portfolio=self._portfolio,
                client=self._client,
                stop_event=self._stop_event
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
        return {
            **self._trader.get_status(),
            **summary,
            "equity": summary["total_equity"],
            "total_fee": summary["total_commission"],
        }

    def shutdown(self) -> None:
        close_signals = self._trader.close_all(reason="shutdown")
        for s in close_signals:
            self._execute_signal(s)
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
