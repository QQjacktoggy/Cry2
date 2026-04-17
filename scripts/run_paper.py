#!/usr/bin/env python3
"""Run paper trading on Binance Testnet."""

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog

from bot.config.env import get_secret, load_env
from bot.config.loader import load_config
from bot.core.clock import RealClock
from bot.core.constants import EventType
from bot.core.event_bus import EventBus
from bot.core.logger import setup_logging
from bot.data.feed_live import LiveFeed
from bot.exchange.binance_rest import BinanceRestClient
from bot.execution.executor_live import LiveExecutor
from bot.monitoring.telegram_notifier import TelegramNotifier
from bot.portfolio.portfolio import Portfolio
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch
from bot.risk.risk_manager import RiskManager
from bot.strategy.registry import StrategyRegistry, register_default_strategies

logger = structlog.get_logger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper trading")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment="paper")
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    logger.info("starting_paper_trading")

    # Initialize components
    event_bus = EventBus()
    clock = RealClock()

    # Exchange
    exchange_cfg = config.get("exchange", {})
    api_key = get_secret(
        exchange_cfg.get("api_key_env", "BINANCE_TESTNET_API_KEY"), required=False
    )
    api_secret = get_secret(
        exchange_cfg.get("api_secret_env", "BINANCE_TESTNET_API_SECRET"), required=False
    )

    client = BinanceRestClient(
        api_key=api_key,
        api_secret=api_secret,
        mode=exchange_cfg.get("mode", "testnet"),
    )

    # Executor
    executor = LiveExecutor(event_bus=event_bus, client=client)
    event_bus.subscribe(EventType.ORDER.value, lambda e: executor.submit_order(e))

    # Portfolio
    portfolio = Portfolio(event_bus=event_bus, initial_capital=config.get("initial_capital", 10000))

    # Risk
    risk_cfg = config.get("risk_limits", {})
    risk_manager = RiskManager(event_bus=event_bus, **{
        k: v for k, v in risk_cfg.items()
        if k in ["max_risk_per_trade_pct", "max_position_value_pct", "max_leverage",
                  "daily_loss_limit_pct", "weekly_loss_limit_pct", "daily_trade_count_limit"]
    })
    kill_switch = KillSwitch(event_bus=event_bus)
    circuit_breaker = CircuitBreaker()
    logger.debug(
        "paper_runtime_components_ready",
        components=[
            portfolio.__class__.__name__,
            risk_manager.__class__.__name__,
            kill_switch.__class__.__name__,
            circuit_breaker.__class__.__name__,
        ],
    )

    # Telegram
    tg_cfg = config.get("telegram", {})
    notifier = TelegramNotifier(
        bot_token=get_secret(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), required=False),
        chat_id=get_secret(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), required=False),
        enabled=tg_cfg.get("enabled", False),
    )

    # Strategies
    register_default_strategies()
    strategies = StrategyRegistry.create_all(config.get("strategies", {}))
    all_symbols = list(set(sym for s in strategies for sym in s.symbols))

    # Data feed
    feed = LiveFeed(
        event_bus=event_bus,
        clock=clock,
        ws_url=exchange_cfg.get("ws_url", "wss://stream.binancefuture.com"),
        symbols=all_symbols,
        timeframe=strategies[0].timeframe if strategies else "1m",
    )

    # Wire strategy on_bar to market events
    def on_market(event):
        for strategy in strategies:
            if event.symbol in strategy.symbols:
                signals = strategy.on_bar(event)
                for signal in signals:
                    event_bus.publish(signal)

    event_bus.subscribe(EventType.MARKET.value, on_market)

    # Handle shutdown
    stop_event = asyncio.Event()

    def handle_signal(*_):
        logger.info("shutdown_signal_received")
        stop_event.set()
        feed.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(
        "paper_trading_started",
        symbols=all_symbols,
        strategies=[s.name for s in strategies],
    )
    notifier.send_sync("🟢 Paper trading started")

    # Run feed
    with suppress(asyncio.CancelledError):
        await feed.start_async()

    logger.info("paper_trading_stopped")
    notifier.send_sync("🔴 Paper trading stopped")


if __name__ == "__main__":
    asyncio.run(main())
