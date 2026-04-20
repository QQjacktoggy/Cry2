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
from bot.execution.executor_live import LiveExecutor
from bot.monitoring.telegram_notifier import TelegramNotifier
from bot.portfolio.portfolio import Portfolio
from bot.portfolio.trade_journal import TradeJournal
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch
from bot.risk.risk_manager import RiskManager
from bot.strategy.bridge import create_v6_strategies, create_v72_strategies

logger = structlog.get_logger(__name__)


def _reconcile_from_binance(
    client: BinanceRestClient,
    journal: TradeJournal,
    log: Any = None,
) -> int:
    """Fetch recent fills from Binance and backfill any missing from journal.

    Called on startup to recover fills that occurred during a disconnect.
    Queries the last 100 trades per symbol found in open trades.
    """
    import datetime as _dt
    _log = log or structlog.get_logger(__name__)

    open_trades = journal.open_trades()
    symbols = set()
    if not open_trades.empty:
        symbols = set(open_trades["symbol"].unique())

    # Also check the last 24h fills from the journal for active symbols
    fills_df = journal.export_fills()
    if not fills_df.empty:
        cutoff = _dt.datetime.now() - _dt.timedelta(hours=24)
        recent = fills_df[fills_df["timestamp"] >= str(cutoff)]
        if not recent.empty:
            symbols.update(recent["symbol"].unique())

    if not symbols:
        _log.info("reconcile_skipped", reason="no_active_symbols")
        return 0

    total_inserted = 0
    for sym in symbols:
        try:
            raw_trades = client.get_account_trades(sym, limit=100)
            mapped = []
            for t in raw_trades:
                mapped.append({
                    "timestamp": _dt.datetime.fromtimestamp(
                        int(t["time"]) / 1000
                    ).isoformat(),
                    "strategy": "unknown",
                    "symbol": t["symbol"],
                    "side": t["side"],
                    "qty": float(t["qty"]),
                    "price": float(t["price"]),
                    "commission": float(t.get("commission", 0)),
                    "comm_asset": t.get("commissionAsset", "USDT"),
                    "order_id": str(t.get("orderId", "")),
                    "client_oid": "",
                    "realized_pnl": float(t.get("realizedPnl", 0)),
                    "source": "reconcile",
                })
            inserted = journal.reconcile(mapped)
            total_inserted += inserted
        except Exception as e:
            _log.warning("reconcile_symbol_failed", symbol=sym, error=str(e))

    if total_inserted > 0:
        _log.info("reconcile_complete", new_fills=total_inserted)
    else:
        _log.info("reconcile_complete", message="no_missing_fills")
    return total_inserted


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run live trading")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--confirm", help="Confirmation string")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital (USDT)")
    parser.add_argument("--version", default="v72", choices=["v6", "v72"], help="Portfolio version")
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
    executor = LiveExecutor(event_bus=event_bus, client=client)
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

    # Reconcile missed fills from Binance (server disconnect recovery)
    _reconcile_from_binance(client, journal, logger)

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
    else:
        strategies = create_v72_strategies(initial_capital=args.capital)
    all_symbols = list(set(s.symbol for s in strategies))
    all_timeframes = list(set(s.timeframe for s in strategies))

    logger.info(
        "strategies_loaded",
        version=args.version,
        count=len(strategies),
        symbols=all_symbols,
        timeframes=all_timeframes,
        capital=f"${args.capital}",
    )

    # Set leverage for each symbol
    for strat in strategies:
        try:
            client.set_leverage(strat.symbol, strat.leverage)
            logger.info("leverage_set", symbol=strat.symbol, leverage=strat.leverage)
        except Exception as e:
            logger.warning("leverage_set_failed", symbol=strat.symbol, error=str(e))

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

    # Wire strategies to market events
    def on_market(event):
        risk_manager.tick_bar()
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

    event_bus.subscribe(EventType.MARKET.value, on_market)

    # Shutdown handler
    stop_event = asyncio.Event()

    def handle_signal(*_):
        logger.info("shutdown_signal_received")
        stop_event.set()
        feed.stop()

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
    with suppress(asyncio.CancelledError):
        await feed.start_async()

    logger.info("live_trading_stopped")
    notifier.send_sync("🔴 LIVE trading stopped")

    # Graceful journal close
    journal.close()
    logger.info("trade_journal_closed")


if __name__ == "__main__":
    asyncio.run(main())
