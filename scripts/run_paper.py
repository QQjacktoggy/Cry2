#!/usr/bin/env python3
"""Run paper trading on Binance Testnet with V7.2 strategies.

Uses VBT bridged strategies (same as live trading) against testnet.
Supports --dry-run mode for local validation without exchange connection.

Usage:
    python scripts/run_paper.py                        # testnet mode
    python scripts/run_paper.py --dry-run              # local validation only
    python scripts/run_paper.py --capital 150 --version v72
"""

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from pathlib import Path

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


def dry_run_validation(strategies, capital: float, version: str) -> None:
    """Validate strategy creation and allocation without exchange connection."""
    print("=" * 60)
    print("🧪 DRY RUN — V7.2 Paper Trading Validation")
    print("=" * 60)

    all_symbols = sorted(set(s.symbol for s in strategies))
    all_timeframes = sorted(set(s.timeframe for s in strategies))
    total_alloc = sum(s._allocation_usd for s in strategies)

    print(f"\n📊 Portfolio Version: {version.upper()}")
    print(f"💰 Capital: ${capital:.0f} USDT")
    print(f"📈 Strategies: {len(strategies)}")
    print(f"🪙 Coins: {', '.join(sym.replace('USDT', '') for sym in all_symbols)}")
    print(f"⏰ Timeframes: {', '.join(all_timeframes)}")
    print(f"💵 Total Allocated: ${total_alloc:.1f} ({total_alloc/capital*100:.0f}%)")

    print(f"\n{'─' * 60}")
    print(f"{'Strategy':<35} {'Symbol':<10} {'TF':<5} {'Alloc':>8} {'Lev':>4}")
    print(f"{'─' * 60}")

    for s in strategies:
        print(f"{s.name:<35} {s.symbol:<10} {s.timeframe:<5} "
              f"${s._allocation_usd:>6.1f} {s.leverage:>3}x")

    print(f"{'─' * 60}")
    print(f"{'TOTAL':<35} {'':10} {'':5} ${total_alloc:>6.1f}")

    # Validation checks
    print(f"\n{'=' * 60}")
    print("✅ Checks:")

    alloc_pct = total_alloc / capital * 100
    check_alloc = 99 <= alloc_pct <= 101
    print(f"  {'✅' if check_alloc else '❌'} Allocation sum: {alloc_pct:.1f}% "
          f"({'OK' if check_alloc else 'MISMATCH'})")

    check_count = len(strategies) == 16 if version == "v72" else len(strategies) > 0
    print(f"  {'✅' if check_count else '❌'} Strategy count: {len(strategies)} "
          f"(expected {'16' if version == 'v72' else '>0'})")

    check_coins = len(all_symbols) == 5 if version == "v72" else len(all_symbols) > 0
    print(f"  {'✅' if check_coins else '❌'} Coin count: {len(all_symbols)} "
          f"(expected {'5' if version == 'v72' else '>0'})")

    # Check each strategy has on_bar method
    all_have_on_bar = all(hasattr(s, "on_bar") for s in strategies)
    print(f"  {'✅' if all_have_on_bar else '❌'} All strategies have on_bar()")

    all_ok = check_alloc and check_count and check_coins and all_have_on_bar
    print(f"\n{'🟢 ALL CHECKS PASSED' if all_ok else '🔴 SOME CHECKS FAILED'}")
    print(f"{'=' * 60}")

    if all_ok:
        print("\n💡 Ready for testnet. Run without --dry-run to connect to Binance Testnet.")
    else:
        print("\n⚠️  Fix issues above before connecting to testnet.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper trading (testnet)")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital (USDT)")
    parser.add_argument("--version", default="v72", choices=["v6", "v72"], help="Portfolio version")
    parser.add_argument("--dry-run", action="store_true", help="Validate locally without exchange")
    args = parser.parse_args()

    load_env()

    # Create bridged strategies
    if args.version == "v6":
        strategies = create_v6_strategies(initial_capital=args.capital)
    else:
        strategies = create_v72_strategies(initial_capital=args.capital)

    if args.dry_run:
        dry_run_validation(strategies, args.capital, args.version)
        return

    config = load_config(args.config, environment="paper")
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    logger.info("starting_paper_trading", version=args.version, capital=args.capital)

    # Initialize components
    event_bus = EventBus()
    clock = RealClock()

    # Exchange (testnet)
    exchange_cfg = config.get("exchange", {})
    api_key = get_secret(
        exchange_cfg.get("api_key_env", "BINANCE_TESTNET_API_KEY"), required=False
    )
    api_secret = get_secret(
        exchange_cfg.get("api_secret_env", "BINANCE_TESTNET_API_SECRET"), required=False
    )

    if not api_key or not api_secret:
        logger.error("testnet_keys_missing",
                      hint="Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET in .env")
        print("\n⚠️  Testnet API keys not found. Use --dry-run for local validation.")
        sys.exit(1)

    client = BinanceRestClient(
        api_key=api_key,
        api_secret=api_secret,
        mode=exchange_cfg.get("mode", "testnet"),
    )

    # Verify connectivity
    try:
        balance = client.get_balance()
        logger.info("testnet_connected", balance=f"${balance:.2f}")
    except Exception as e:
        logger.error("testnet_connection_failed", error=str(e))
        sys.exit(1)

    # Executor
    executor = LiveExecutor(event_bus=event_bus, client=client)
    event_bus.subscribe(EventType.ORDER.value, lambda e: executor.submit_order(e))

    # Portfolio
    portfolio = Portfolio(event_bus=event_bus, initial_capital=args.capital)

    # Trade Journal — persists every fill to SQLite (survives restarts)
    journal = TradeJournal("./data/paper_trades.db")
    journal.attach(event_bus)
    resume_info = journal.summary()
    if resume_info["total_fills"] > 0:
        logger.info(
            "trade_journal_resumed",
            previous_fills=resume_info["total_fills"],
            previous_trades=resume_info["total_trades"],
            net_pnl=resume_info["net_pnl"],
        )

    # Risk
    risk_cfg = config.get("risk_limits", {})
    risk_manager = RiskManager(event_bus=event_bus, **{
        k: v for k, v in risk_cfg.items()
        if k in ["max_risk_per_trade_pct", "max_position_value_pct", "max_leverage",
                  "daily_loss_limit_pct", "weekly_loss_limit_pct", "daily_trade_count_limit"]
    })
    kill_switch = KillSwitch(event_bus=event_bus)
    circuit_breaker = CircuitBreaker()

    # Telegram
    tg_cfg = config.get("telegram", {})
    notifier = TelegramNotifier(
        bot_token=get_secret(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), required=False),
        chat_id=get_secret(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), required=False),
        enabled=tg_cfg.get("enabled", False),
    )

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
        except Exception as e:
            logger.warning("leverage_set_failed", symbol=strat.symbol, error=str(e))

    # Data feed
    primary_tf = min(all_timeframes, key=lambda t: {"1h": 1, "4h": 4, "8h": 8, "1d": 24}.get(t, 4))
    feed = LiveFeed(
        event_bus=event_bus,
        clock=clock,
        ws_url=exchange_cfg.get("ws_url", "wss://stream.binancefuture.com"),
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
        "paper_trading_started",
        strategies=[s.name for s in strategies],
        symbols=all_symbols,
    )
    notifier.send_sync(
        f"🟡 PAPER trading started (testnet)\n"
        f"Version: {args.version.upper()}\n"
        f"Capital: ${args.capital} USDT\n"
        f"Strategies: {len(strategies)}\n"
        f"Coins: {', '.join(s.replace('USDT','') for s in sorted(all_symbols))}"
    )

    # Run feed
    with suppress(asyncio.CancelledError):
        await feed.start_async()

    logger.info("paper_trading_stopped")
    notifier.send_sync("🔴 Paper trading stopped")

    # Graceful journal close
    journal.close()
    logger.info("trade_journal_closed")


if __name__ == "__main__":
    asyncio.run(main())
