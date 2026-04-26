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
from collections import deque
import platform
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
import urllib.request

# Add src/ to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import structlog
import yaml
from dotenv import load_dotenv

from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent, MarketEvent
from jackbot.core.constants import TradingMode
from jackbot.exchange.client import BinanceClient
from jackbot.exchange.feed import KlineFeed
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

        # WebSocket feed
        self._feed = KlineFeed(symbols=symbols, timeframe=timeframe, testnet=testnet)
        self._feed.on_bar = self._on_bar

        # Runtime telemetry for Telegram reports
        self._started_at = datetime.now(UTC)
        self._runtime = {
            "signals_total": 0,
            "orders_placed": 0,
            "orders_failed": 0,
            "cancel_requests": 0,
            "last_error": "",
        }
        self._recent_events: deque[dict] = deque(maxlen=100)

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

    def _on_bar(self, event: MarketEvent) -> None:
        """Handle each completed kline bar."""
        signals = self._trader.on_bar(event)

        if self._dry_run:
            for s in signals:
                logger.info("dry_run_signal", side=s.side, price=s.price,
                            qty=s.quantity, grid_id=s.grid_id, level=s.level_index)
            return

        # Execute signals on exchange
        for signal in signals:
            self._execute_signal(signal)

    def _record_event(self, kind: str, data: dict | None = None) -> None:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "data": data or {},
        }
        self._recent_events.append(payload)

    def _execute_signal(self, signal: GridSignalEvent) -> None:
        """Execute a grid signal on the exchange."""
        self._runtime["signals_total"] += 1
        try:
            if signal.cancel_order_id:
                self._runtime["cancel_requests"] += 1
                self._client.cancel_order(signal.symbol, signal.cancel_order_id)
                self._record_event("cancel", {
                    "symbol": signal.symbol,
                    "order_id": signal.cancel_order_id,
                })
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
            self._runtime["orders_placed"] += 1
            self._record_event("order", {
                "symbol": signal.symbol,
                "side": signal.side,
                "order_type": signal.order_type,
                "price": signal.price,
                "quantity": signal.quantity,
                "grid_id": signal.grid_id,
                "order_id": order_id,
            })

            # For limit orders, track the order ID on the grid level
            grid = self._trader._engine.get_grid(signal.grid_id)
            if grid and 0 <= signal.level_index < len(grid.levels):
                level = grid.levels[signal.level_index]
                if signal.side == "BUY":
                    level.buy_order_id = order_id
                else:
                    level.sell_order_id = order_id

        except Exception as e:
            self._runtime["orders_failed"] += 1
            self._runtime["last_error"] = str(e)
            self._record_event("error", {
                "symbol": signal.symbol,
                "side": signal.side,
                "error": str(e),
            })
            logger.error("signal_execution_error", error=str(e), signal=signal.model_dump())
            self._telegram.notify_error("下單失敗", str(e))

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
            leverage=0,
        )
        self._portfolio.record_trade(trade)
        self._record_event("profit", {
            "symbol": event.symbol,
            "profit_usd": event.profit_usd,
            "grid_id": event.grid_id,
            "level_index": event.level_index,
        })

        self._telegram.notify_grid_profit(
            profit=event.profit_usd,
            total=self._trader.daily_profit,
            target=self._trader._cfg.daily_profit_target_usd,
        )

    async def run(self) -> None:
        """Main loop — warmup with historical klines, then stream live bars."""
        logger.info("jackbot_starting")

        if not self._dry_run:
            # Diagnostic: check environment first
            api_key = os.getenv(self._cfg["exchange"].get("api_key_env", ""), "")
            api_secret = os.getenv(self._cfg["exchange"].get("api_secret_env", ""), "")

            if not api_key or not api_secret:
                logger.error(
                    "missing_api_credentials",
                    api_key_env=self._cfg["exchange"].get("api_key_env", ""),
                    api_secret_env=self._cfg["exchange"].get("api_secret_env", ""),
                    api_key_present=bool(api_key),
                    api_secret_present=bool(api_secret),
                )
                logger.error("startup_blocked_missing_credentials")
                self._telegram.send("❌ <b>啟動失敗</b>：缺少 API Key/Secret\n檢查 GCP Secret Manager 或 .env 設定")
                return

            # Test connectivity
            try:
                latency = self._client.ping()
                balance = self._client.get_balance()
                logger.info("exchange_connected", latency_ms=latency, balance=balance)
            except Exception as e:
                logger.error("exchange_connection_failed", error=str(e),
                            base_url=self._cfg["exchange"].get("base_url", ""))
                self._telegram.send(f"❌ <b>交易所連線失敗</b>：{str(e)}")
                return

            # Load symbol precision info (qty/price decimal places)
            self._client.load_symbol_info(self._trader._cfg.symbols)

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
                get_runner_snapshot=self.get_telegram_snapshot,
                close_all_now=self.close_all_now,
                halt_trading=self.halt_trading,
                resume_trading=self.resume_trading,
                set_mode=self.set_mode,
                stop_runner=self.request_shutdown,
                stop_event=self._stop_event
            )
            
            await asyncio.gather(
                self._feed.start(),
                commander.run()
            )
        else:
            logger.info("dry_run_mode — simulating with historical data")
            for symbol in self._trader._cfg.symbols:
                await self._warmup_symbol(symbol)
            logger.info("dry_run_complete", status=self._trader.get_status())

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
        return {
            **self._trader.get_status(),
            **self._portfolio.get_summary(),
            "runtime": dict(self._runtime),
            "started_at": self._started_at.isoformat(),
        }

    def _build_exchange_snapshot(self) -> dict:
        snapshot: dict = {
            "balance": None,
            "open_orders": {},
            "positions": {},
            "error": "",
        }
        try:
            snapshot["balance"] = self._client.get_balance()
            for symbol in self._trader._cfg.symbols:
                orders = self._client.get_open_orders(symbol)
                pos = self._client.get_position(symbol)
                qty = float(pos.get("positionAmt", 0.0)) if pos else 0.0
                entry = float(pos.get("entryPrice", 0.0)) if pos else 0.0
                upnl = float(pos.get("unRealizedProfit", 0.0)) if pos else 0.0
                snapshot["open_orders"][symbol] = len(orders)
                snapshot["positions"][symbol] = {
                    "qty": qty,
                    "entry": entry,
                    "upnl": upnl,
                }
        except Exception as e:
            snapshot["error"] = str(e)
        return snapshot

    def _build_gcp_snapshot(self) -> dict:
        """Collect VM/container runtime and GCP metadata visible from this process."""

        def _meta(path: str) -> str:
            url = f"http://metadata.google.internal/computeMetadata/v1/{path}"
            req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.read().decode().strip()

        gcp: dict = {
            "available": False,
            "project_id": "",
            "instance_name": "",
            "instance_id": "",
            "zone": "",
            "machine_type": "",
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "cpu_load_1m": 0.0,
            "disk": {},
            "memory": {},
            "error": "",
        }

        # CPU load (Unix only)
        try:
            gcp["cpu_load_1m"] = round(float(os.getloadavg()[0]), 3)
        except Exception:
            gcp["cpu_load_1m"] = 0.0

        # Disk usage of container root filesystem
        try:
            stats = os.statvfs("/")
            total = stats.f_frsize * stats.f_blocks
            free = stats.f_frsize * stats.f_bavail
            used = total - free
            gcp["disk"] = {
                "total_gb": round(total / (1024 ** 3), 2),
                "used_gb": round(used / (1024 ** 3), 2),
                "free_gb": round(free / (1024 ** 3), 2),
                "used_pct": round((used / total) * 100, 2) if total > 0 else 0.0,
            }
        except Exception as e:
            gcp["disk"] = {"error": str(e)}

        # Memory from /proc/meminfo
        try:
            kv: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":", 1)
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    if val.isdigit():
                        kv[key] = int(val)  # kB
            total_kb = kv.get("MemTotal", 0)
            avail_kb = kv.get("MemAvailable", 0)
            used_kb = max(total_kb - avail_kb, 0)
            gcp["memory"] = {
                "total_mb": round(total_kb / 1024, 2),
                "used_mb": round(used_kb / 1024, 2),
                "available_mb": round(avail_kb / 1024, 2),
                "used_pct": round((used_kb / total_kb) * 100, 2) if total_kb > 0 else 0.0,
            }
        except Exception as e:
            gcp["memory"] = {"error": str(e)}

        # GCE metadata
        try:
            gcp["project_id"] = _meta("project/project-id")
            gcp["instance_name"] = _meta("instance/name")
            gcp["instance_id"] = _meta("instance/id")
            gcp["zone"] = _meta("instance/zone").split("/")[-1]
            gcp["machine_type"] = _meta("instance/machine-type").split("/")[-1]
            gcp["available"] = True
        except Exception as e:
            gcp["error"] = str(e)

        return gcp

    def get_telegram_snapshot(self) -> dict:
        """Build a rich snapshot for Telegram reports and commands."""
        now = datetime.now(UTC)
        uptime_sec = int((now - self._started_at).total_seconds())
        return {
            "timestamp": now.isoformat(),
            "uptime_sec": uptime_sec,
            "trader": self._trader.get_status(),
            "portfolio": self._portfolio.get_summary(),
            "portfolio_symbol_pnl": self._portfolio.get_symbol_pnl(),
            "portfolio_recent_trades": self._portfolio.get_recent_trades(limit=15),
            "exchange": self._build_exchange_snapshot(),
            "gcp": self._build_gcp_snapshot(),
            "runtime": dict(self._runtime),
            "recent_events": list(self._recent_events)[-30:],
            "symbols": list(self._trader._cfg.symbols),
            "timeframe": self._trader._cfg.timeframe,
        }

    def close_all_now(self, reason: str = "telegram_manual_close") -> int:
        """Close all active grids immediately and execute generated cancel signals."""
        signals = self._trader.close_all(reason=reason)
        for signal in signals:
            self._execute_signal(signal)
        self._record_event("manual_close_all", {"reason": reason, "signal_count": len(signals)})
        return len(signals)

    def halt_trading(self, reason: str = "telegram_manual_halt") -> None:
        self._trader.manual_halt(reason)
        self._record_event("manual_halt", {"reason": reason})

    def resume_trading(self) -> bool:
        ok = self._trader.manual_resume()
        self._record_event("manual_resume", {"ok": ok})
        return ok

    def set_mode(self, mode: str) -> bool:
        if mode not in {"aggressive", "conservative"}:
            return False
        target_mode = TradingMode.AGGRESSIVE if mode == "aggressive" else TradingMode.CONSERVATIVE
        self._trader.set_mode(target_mode)
        self._record_event("manual_mode", {"mode": mode})
        return True

    def request_shutdown(self, reason: str = "telegram_manual_shutdown") -> None:
        self._record_event("manual_shutdown", {"reason": reason})
        self._stop_event.set()

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
    parser.add_argument("--diagnose", action="store_true", help="Run startup diagnostics and exit")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    config = load_config(args.config)

    if args.diagnose:
        # Diagnostic mode: check all prerequisites
        import json
        diagnostics = {
            "timestamp": datetime.now(UTC).isoformat(),
            "config_loaded": bool(config),
            "exchange_config": config.get("exchange", {}),
            "trading_config": config.get("trading", {}),
            "environment_variables": {
                "BINANCE_TESTNET_API_KEY": "✓ SET" if os.getenv("BINANCE_TESTNET_API_KEY") else "✗ MISSING",
                "BINANCE_TESTNET_API_SECRET": "✓ SET" if os.getenv("BINANCE_TESTNET_API_SECRET") else "✗ MISSING",
                "TELEGRAM_BOT_TOKEN": "✓ SET" if os.getenv("TELEGRAM_BOT_TOKEN") else "✗ MISSING",
                "TELEGRAM_CHAT_ID": "✓ SET" if os.getenv("TELEGRAM_CHAT_ID") else "✗ MISSING",
            }
        }

        # Try to connect to exchange
        try:
            exchange = config.get("exchange", {})
            test_client = BinanceClient(
                api_key=os.getenv(exchange.get("api_key_env", ""), ""),
                api_secret=os.getenv(exchange.get("api_secret_env", ""), ""),
                testnet=exchange.get("mode", "testnet") == "testnet",
                base_url=exchange.get("base_url", ""),
            )
            latency = test_client.ping()
            balance = test_client.get_balance()
            diagnostics["exchange_connection"] = {
                "status": "✓ CONNECTED",
                "latency_ms": latency,
                "balance_usd": balance,
            }
            test_client.close()
        except Exception as e:
            diagnostics["exchange_connection"] = {
                "status": "✗ FAILED",
                "error": str(e),
            }

        print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
        return

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
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        runner.shutdown()


if __name__ == "__main__":
    main()
