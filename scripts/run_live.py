#!/usr/bin/env python3
"""Run live trading on Binance Futures.

Uses V7.2 portfolio strategies bridged from backtest system.
Requires explicit confirmation to prevent accidental execution.

Usage:
    python scripts/run_live.py --confirm CONFIRM_LIVE_TRADING
    python scripts/run_live.py --confirm CONFIRM_LIVE_TRADING --capital 150
    python scripts/run_live.py --confirm CONFIRM_LIVE_TRADING --version v6
"""

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

from bot.config.env import get_secret, load_env
from bot.config.loader import load_config
from bot.core.clock import RealClock
from bot.core.constants import EventType
from bot.core.event_bus import EventBus
from bot.core.logger import setup_logging
from bot.data.feed_live import LiveFeed
from bot.exchange.binance_rest import BinanceRestClient
from bot.exchange.user_data_stream import UserDataStream
from bot.execution.executor_live import LiveExecutor
from bot.monitoring.health_state import HealthStateWriter
from bot.monitoring.telegram_notifier import TelegramNotifier
from bot.portfolio.portfolio import Portfolio
from bot.portfolio.reconcile import reconcile_from_binance
from bot.portfolio.trade_journal import TradeJournal
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch
from bot.risk.risk_manager import RiskManager
from bot.strategy.bridge import (
    create_v6_strategies,
    create_v72_strategies,
    create_v74_strategies,
)

logger = structlog.get_logger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run live trading")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--confirm", help="Confirmation string")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital (USDT)")
    parser.add_argument(
        "--version",
        default="v72",
        choices=["v6", "v72", "v74"],
        help="Portfolio version",
    )
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment="live")

    safety = config.get("safety", {})
    required_string = safety.get("confirmation_string", "CONFIRM_LIVE_TRADING")

    if safety.get("require_confirmation", True) and args.confirm != required_string:
        print("=" * 60)
        print("⚠️  LIVE TRADING MODE")
        print("=" * 60)
        print("This will trade with REAL MONEY on Binance Mainnet.")
        print(f"To confirm, run with: --confirm {required_string}")
        print(f"Capital: ${args.capital} USDT")
        print("=" * 60)
        sys.exit(1)

    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=True)
    logger.info("starting_live_trading", capital=args.capital)

    # Initialize core components
    event_bus = EventBus()
    clock = RealClock()

    # Exchange (LIVE mode)
    exchange_cfg = config.get("exchange", {})
    api_key = get_secret(
        exchange_cfg.get("api_key_env", "BINANCE_API_KEY"), required=True
    )
    api_secret = get_secret(
        exchange_cfg.get("api_secret_env", "BINANCE_API_SECRET"), required=True
    )

    client = BinanceRestClient(
        api_key=api_key,
        api_secret=api_secret,
        mode="live",
    )

    # Verify connectivity
    try:
        balance = client.get_balance()
        logger.info("exchange_connected", balance=f"${balance:.2f}")
        if balance < args.capital:
            logger.warning("insufficient_balance",
                           available=balance, required=args.capital)
    except Exception as e:
        logger.error("exchange_connection_failed", error=str(e))
        sys.exit(1)

    # Executor
    executor = LiveExecutor(
        event_bus=event_bus,
        client=client,
        publish_market_fills=False,
    )
    event_bus.subscribe(EventType.ORDER.value, lambda e: executor.submit_order(e))

    # Portfolio
    portfolio = Portfolio(
        event_bus=event_bus,
        initial_capital=args.capital,
    )

    # Trade Journal — persists every fill to SQLite (survives restarts)
    journal = TradeJournal("./data/live_trades.db")
    journal.attach(event_bus)
    resume_info = journal.summary()
    if resume_info["total_fills"] > 0:
        logger.info(
            "trade_journal_resumed",
            previous_fills=resume_info["total_fills"],
            previous_trades=resume_info["total_trades"],
            net_pnl=resume_info["net_pnl"],
        )

    # Risk management
    risk_cfg = config.get("risk_limits", {})
    risk_manager = RiskManager(event_bus=event_bus, **{
        k: v for k, v in risk_cfg.items()
        if k in ["max_risk_per_trade_pct", "max_position_value_pct", "max_leverage",
                  "daily_loss_limit_pct", "weekly_loss_limit_pct", "daily_trade_count_limit"]
    })
    kill_switch = KillSwitch(event_bus=event_bus)
    circuit_breaker = CircuitBreaker()

    # Telegram notifications
    tg_cfg = config.get("telegram", {})
    notifier = TelegramNotifier(
        bot_token=get_secret(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), required=False),
        chat_id=get_secret(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), required=False),
        enabled=tg_cfg.get("enabled", False),
    )

    # Create bridged strategies
    if args.version == "v6":
        strategies = create_v6_strategies(initial_capital=args.capital)
    elif args.version == "v74":
        strategies = create_v74_strategies(initial_capital=args.capital)
    else:
        strategies = create_v72_strategies(initial_capital=args.capital)
    all_symbols = list(set(s.symbol for s in strategies))
    all_timeframes = list(set(s.timeframe for s in strategies))
    strategy_names = [s.name for s in strategies]

    logger.info(
        "strategies_loaded",
        version=args.version,
        count=len(strategies),
        symbols=all_symbols,
        timeframes=all_timeframes,
        capital=f"${args.capital}",
    )

    health = HealthStateWriter("./data/health.json")
    state: dict[str, Any] = {"circuit_breaker_symbol": ""}

    def _sync_health(**updates: Any) -> None:
        state.update(updates)
        health.update(
            ws_connected=bool(state.get("ws_connected", False)),
            user_data_stream_ok=bool(state.get("user_data_stream_ok", False)),
            last_bar_ts=state.get("last_bar_ts"),
            last_reconcile_ts=state.get("last_reconcile_ts"),
            kill_switch_triggered=kill_switch.is_triggered,
            circuit_breaker_tripped=circuit_breaker.is_tripped,
            circuit_breaker_symbol=state.get("circuit_breaker_symbol", ""),
            api_latency_ms=state.get("api_latency_ms"),
            risk_manager=risk_manager.get_status(),
            regime=risk_manager.get_regime_status(),
        )

    _sync_health(ws_connected=False, user_data_stream_ok=False)

    # Data feed (multi-timeframe: use smallest common timeframe)
    primary_tf = min(all_timeframes, key=lambda t: {"1h": 1, "4h": 4, "8h": 8, "1d": 24}.get(t, 4))
    feed = LiveFeed(
        event_bus=event_bus,
        clock=clock,
        ws_url=exchange_cfg.get("ws_url", "wss://fstream.binance.com"),
        symbols=all_symbols,
        timeframe=primary_tf,
        rest_client=client,
        funding_poll_interval=300,
    )
    feed.on_status_change = lambda connected: _sync_health(ws_connected=connected)

    user_data_stream = UserDataStream(
        event_bus=event_bus,
        rest_client=client,
        ws_url=exchange_cfg.get("ws_url", "wss://fstream.binance.com"),
        known_strategies=strategy_names,
        on_status_change=lambda healthy: _sync_health(user_data_stream_ok=healthy),
    )

    def _apply_symbol_leverage() -> None:
        desired_leverage_by_symbol: dict[str, int] = {}
        for strat in strategies:
            desired_leverage_by_symbol[strat.symbol] = max(
                desired_leverage_by_symbol.get(strat.symbol, 1),
                min(int(round(risk_manager.effective_max_leverage)), int(strat.leverage)),
            )

        for symbol, leverage in desired_leverage_by_symbol.items():
            try:
                client.set_leverage(symbol, leverage)
                logger.info("leverage_set", symbol=symbol, leverage=leverage)
            except Exception as e:
                logger.warning("leverage_set_failed", symbol=symbol, error=str(e))

    _apply_symbol_leverage()

    def _do_reconcile() -> None:
        latency_ms = client.ping()
        kill_switch.check_api_latency(latency_ms)
        reconcile_from_binance(client, journal, logger, known_strategies=strategy_names)
        _sync_health(
            api_latency_ms=round(latency_ms, 2),
            last_reconcile_ts=datetime.now(UTC).isoformat(),
        )

    feed.on_reconnect = _do_reconcile
    feed.periodic_sync_fn = _do_reconcile
    _do_reconcile()

    # Wire strategies to market events
    def on_market(event):
        risk_manager.set_equity(portfolio.equity)
        for strategy in strategies:
            strategy.set_equity(portfolio.equity)

        risk_manager.update_market_data(event.high, event.low, event.close)
        risk_manager.tick_bar()
        if circuit_breaker.is_tripped_at(event.timestamp):
            _sync_health(
                last_bar_ts=event.timestamp.isoformat(),
                circuit_breaker_symbol=state.get("circuit_breaker_symbol", event.symbol),
            )
            return
        if not circuit_breaker.check_bar(event):
            _sync_health(
                last_bar_ts=event.timestamp.isoformat(),
                circuit_breaker_symbol=event.symbol,
            )
            return

        leverage_before = state.get("applied_effective_leverage")
        for strategy in strategies:
            if event.symbol in strategy.symbols:
                try:
                    signals = strategy.on_bar(event)
                    for sig in signals:
                        logger.info("signal_generated",
                                    strategy=strategy.name,
                                    side=sig.side.value,
                                    symbol=sig.symbol,
                                    reason=sig.reason)
                        event_bus.publish(sig)
                except Exception as e:
                    logger.error("strategy_error",
                                 strategy=strategy.name,
                                 error=str(e))

        applied_effective_leverage = round(risk_manager.effective_max_leverage, 2)
        if leverage_before != applied_effective_leverage:
            _apply_symbol_leverage()
        _sync_health(
            last_bar_ts=event.timestamp.isoformat(),
            circuit_breaker_symbol="",
            applied_effective_leverage=applied_effective_leverage,
        )

    event_bus.subscribe(EventType.MARKET.value, on_market)

    def on_fill(event):
        for strategy in strategies:
            if event.strategy_name == strategy.name:
                strategy.on_fill(event)
        _sync_health()

    event_bus.subscribe(EventType.FILL.value, on_fill)

    # Shutdown handler
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_signal(*_):
        logger.info("shutdown_signal_received")
        stop_event.set()
        feed.stop()
        loop.create_task(user_data_stream.stop())
        _sync_health(ws_connected=False, user_data_stream_ok=False)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start
    logger.info(
        "live_trading_started",
        strategies=[s.name for s in strategies],
        symbols=all_symbols,
    )
    notifier.send_sync(
        f"🟢 LIVE trading started\n"
        f"Capital: ${args.capital} USDT\n"
        f"Strategies: {len(strategies)}\n"
        f"Coins: {', '.join(s.replace('USDT','') for s in all_symbols)}"
    )

    # Run feed
    user_data_task = asyncio.create_task(user_data_stream.start())
    try:
        with suppress(asyncio.CancelledError):
            await feed.start_async()
    finally:
        await user_data_stream.stop()
        with suppress(asyncio.CancelledError):
            await user_data_task

    logger.info("live_trading_stopped")
    notifier.send_sync("🔴 LIVE trading stopped")

    # Graceful journal close
    _sync_health(ws_connected=False, user_data_stream_ok=False)
    journal.close()
    logger.info("trade_journal_closed")


if __name__ == "__main__":
    asyncio.run(main())
