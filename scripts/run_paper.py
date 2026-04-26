#!/usr/bin/env python3
"""Run paper trading on Binance Testnet with bridged portfolio strategies.

Uses VBT bridged strategies (same as live trading) against testnet.
Supports --dry-run mode for local validation without exchange connection.

Usage:
    python scripts/run_paper.py                        # testnet mode
    python scripts/run_paper.py --dry-run              # local validation only
    python scripts/run_paper.py --capital 150 --version v74
"""

import argparse
import asyncio
import os
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

from bot.cloud.cloud_logging import emit_lifecycle_event, setup_cloud_logging
from bot.cloud.metrics import CloudMetrics
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
from bot.monitoring.runtime_smoke import build_runtime_smoke_report, print_runtime_smoke_report
from bot.monitoring.telegram_commander import TelegramCommander
from bot.monitoring.telegram_notifier import TelegramNotifier
from bot.portfolio.portfolio import Portfolio
from bot.portfolio.reconcile import reconcile_from_binance
from bot.portfolio.trade_journal import TradeJournal
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.kill_switch import KillSwitch
from bot.risk.risk_manager import RiskManager
from bot.runtime import build_deploy_meta
from bot.runtime.config_snapshot import write_run_snapshot
from bot.runtime.multi_timeframe import event_matches_strategy, group_symbols_by_timeframe, sort_timeframes
from bot.runtime.preflight import format_preflight, run_preflight
from bot.runtime.review_bundle import export_review_bundle
from bot.runtime.paper_reset import clear_runtime_state, flatten_testnet_state
from bot.runtime.single_instance import SingleInstance, SingleInstanceError
from bot.strategy.bridge import (
    create_v6_strategies,
    create_v72_strategies,
    create_v74_strategies,
)
from bot.strategy.regime_composite_config import RegimeCompositeConfig
from bot.strategy.regime_composite_live import RegimeCompositeLiveStrategy

logger = structlog.get_logger(__name__)
_HEALTH_HEARTBEAT_SEC = 30.0
_USER_DATA_RECONNECT_GRACE_SEC = 180.0


def dry_run_validation(
    strategies,
    capital: float,
    version: str,
    *,
    health_path: str,
) -> None:
    """Validate runtime wiring without exchange connectivity."""
    report = build_runtime_smoke_report(
        strategies=strategies,
        capital=capital,
        version=version,
        health_path=health_path,
    )
    print_runtime_smoke_report(report, runtime_label="PAPER")

    if report["all_checks_passed"]:
        print("\n💡 Ready for testnet. Run without --dry-run to connect to Binance Testnet.")
    else:
        print("\n⚠️  Fix issues above before connecting to testnet.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper trading (testnet)")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital (USDT)")
    parser.add_argument(
        "--version",
        default="v72",
        choices=["v6", "v72", "v74", "rc"],
        help="Portfolio version (rc = regime composite)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate locally without exchange")
    parser.add_argument(
        "--health-path",
        default="./data/health.json",
        help="Path for runtime health snapshot output",
    )
    parser.add_argument(
        "--instance-lock",
        default="./data/paper.lock",
        help="Single-instance lock file path",
    )
    args = parser.parse_args()

    # Create bridged strategies
    # Note: for "rc" version, strategies are created after risk_manager is
    # instantiated (needs risk_manager reference). Placeholder set here.
    _rc_mode = args.version == "rc"
    if args.version == "v6":
        strategies = create_v6_strategies(initial_capital=args.capital)
    elif args.version == "v74":
        strategies = create_v74_strategies(initial_capital=args.capital)
    elif _rc_mode:
        strategies = []  # will be populated after risk_manager is ready
    else:
        strategies = create_v72_strategies(initial_capital=args.capital)

    if args.dry_run:
        dry_run_validation(
            strategies,
            args.capital,
            args.version,
            health_path=args.health_path,
        )
        return

    load_env()
    config = load_config(args.config, environment="paper")
    data_dir = Path(config.get("paths", {}).get("data_dir", "./data"))
    if args.health_path == "./data/health.json":
        args.health_path = str(data_dir / "health.json")
    if args.instance_lock == "./data/paper.lock":
        args.instance_lock = str(data_dir / "paper.lock")

    try:
        instance_guard = SingleInstance(args.instance_lock).__enter__()
    except SingleInstanceError as e:
        print(f"⚠️  {e}", file=sys.stderr)
        sys.exit(2)

    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)
    setup_cloud_logging()  # no-op off GCP

    deploy_meta = build_deploy_meta(config=config, version=args.version, environment="paper")
    journal_path = data_dir / "paper_trades.db"
    runs_dir = data_dir / "runs"
    run_dir = write_run_snapshot(config=config, meta=deploy_meta, runs_dir=str(runs_dir))

    metrics = CloudMetrics()

    exchange_cfg_preview = config.get("exchange", {})
    preflight_report = run_preflight(
        required_secrets=[
            exchange_cfg_preview.get("api_key_env", "BINANCE_TESTNET_API_KEY"),
            exchange_cfg_preview.get("api_secret_env", "BINANCE_TESTNET_API_SECRET"),
        ],
        writable_paths=[
            str(data_dir),
            args.health_path,
            str(run_dir),
            str(journal_path),
        ],
        exchange_client=None,
        expected_environment="paper",
        config_environment=str(config.get("environment", "paper")),
        secret_resolver=lambda name: get_secret(name, required=True),
    )
    print(format_preflight(preflight_report))
    if not preflight_report.all_passed:
        emit_lifecycle_event(
            "preflight_failed",
            run_id=deploy_meta.run_id,
            environment="paper",
            checks=preflight_report.checks,
        )
        instance_guard.__exit__(None, None, None)
        sys.exit(3)

    emit_lifecycle_event(
        "startup",
        run_id=deploy_meta.run_id,
        environment="paper",
        version=args.version,
        git_sha=deploy_meta.git_sha,
        config_fingerprint=deploy_meta.config_fingerprint,
        capital=args.capital,
    )

    logger.info(
        "starting_paper_trading",
        version=args.version,
        capital=args.capital,
        run_id=deploy_meta.run_id,
        config_fingerprint=deploy_meta.config_fingerprint,
        git_sha=deploy_meta.git_sha,
        image_tag=deploy_meta.image_tag,
        run_dir=str(run_dir),
    )

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
    executor = LiveExecutor(
        event_bus=event_bus,
        client=client,
        publish_market_fills=False,
    )
    event_bus.subscribe(EventType.ORDER.value, lambda e: executor.submit_order(e))

    # Portfolio
    portfolio = Portfolio(event_bus=event_bus, initial_capital=args.capital)

    # Trade Journal — persists every fill to SQLite (survives restarts)
    journal = TradeJournal(journal_path, deploy_meta=deploy_meta)
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
        if k in [
            "max_risk_per_trade_pct",
            "max_position_value_pct",
            "max_leverage",
            "daily_loss_limit_pct",
            "weekly_loss_limit_pct",
            "daily_trade_count_limit",
            "max_drawdown_pct",
            "drawdown_cooldown_bars",
            "max_consecutive_losses",
            "loss_cooldown_bars",
            "daily_profit_target_usd",
        ]
    })

    # Create regime composite strategy now that risk_manager is ready
    if _rc_mode:
        rc_cfg_dict = config.get("strategies", {}).get("regime_composite", {})
        if not rc_cfg_dict.get("enabled", True):
            logger.warning("regime_composite_disabled_in_config")
        else:
            rc_cfg = RegimeCompositeConfig.from_dict({
                **rc_cfg_dict,
                "allocation_usd": rc_cfg_dict.get("allocation_usd", args.capital),
            })
            rc_strategy = RegimeCompositeLiveStrategy(
                risk_manager=risk_manager,
                config=rc_cfg,
            )
            strategies = [rc_strategy]
            logger.info(
                "regime_composite_strategy_created",
                symbols=rc_cfg.symbols,
                timeframe=rc_cfg.timeframe,
                daily_target=rc_cfg.daily_profit_target_usd,
            )
    kill_switch = KillSwitch(event_bus=event_bus)
    circuit_breaker = CircuitBreaker()

    # Telegram
    tg_cfg = config.get("telegram", {})
    notifier = TelegramNotifier(
        bot_token=get_secret(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), required=False),
        chat_id=get_secret(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), required=False),
        enabled=tg_cfg.get("enabled", False),
    )

    # Collect symbols — strategies may expose .symbol (str) or .symbols (list)
    _sym_set: set[str] = set()
    for s in strategies:
        if hasattr(s, "symbols") and isinstance(s.symbols, list):
            _sym_set.update(s.symbols)
        elif hasattr(s, "symbol") and s.symbol:
            _sym_set.add(s.symbol)
    all_symbols = list(_sym_set)
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

    health = HealthStateWriter(args.health_path)
    state: dict[str, Any] = {"circuit_breaker_symbol": ""}

    def _sync_health(**updates: Any) -> None:
        state.update(updates)
        health.update(
            ws_connected=bool(state.get("ws_connected", False)),
            user_data_stream_ok=bool(state.get("user_data_stream_ok", False)),
            user_data_stream_reconnecting=bool(state.get("user_data_stream_reconnecting", False)),
            user_data_stream_grace_until=state.get("user_data_stream_grace_until"),
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

    def _report_risk_observability() -> None:
        status = risk_manager.get_status()
        metrics.report_pnl(float(status.get("daily_pnl", 0.0)))
        metrics.report_drawdown_halt(bool(status.get("drawdown_halted", False)))

        transitions = [
            ("daily_halted", "daily_loss_halt"),
            ("weekly_halted", "weekly_loss_halt"),
            ("drawdown_halted", "drawdown_halt"),
        ]
        for key, event_base in transitions:
            prev = bool(state.get(f"obs_{key}", False))
            curr = bool(status.get(key, False))
            if curr == prev:
                continue
            state[f"obs_{key}"] = curr
            phase = "activated" if curr else "cleared"
            event_name = f"{event_base}_{phase}"
            details = {
                "daily_pnl": status.get("daily_pnl", 0.0),
                "weekly_pnl": status.get("weekly_pnl", 0.0),
                "drawdown_pct": status.get("drawdown_pct", 0.0),
                "drawdown_cooldown_remaining": status.get("drawdown_cooldown_remaining", 0),
            }
            journal.record_runtime_event(
                event_name,
                severity="critical" if curr else "info",
                details=details,
            )
            emit_lifecycle_event(
                event_name,
                run_id=deploy_meta.run_id,
                environment="paper",
                **details,
            )

    # Data feed
    timeframes = sort_timeframes(all_timeframes)
    primary_tf = timeframes[0]
    symbols_by_timeframe = group_symbols_by_timeframe(strategies)
    feed_status_by_timeframe = {timeframe: False for timeframe in symbols_by_timeframe}
    feeds: list[LiveFeed] = []

    def _on_ws_status(timeframe: str, connected: bool) -> None:
        feed_status_by_timeframe[timeframe] = connected
        all_connected = bool(feed_status_by_timeframe) and all(feed_status_by_timeframe.values())
        _sync_health(ws_connected=all_connected)
        metrics.report_ws_connected(all_connected)
        if not connected:
            emit_lifecycle_event(
                "ws_disconnect",
                run_id=deploy_meta.run_id,
                environment="paper",
                timeframe=timeframe,
            )
            journal.record_runtime_event(
                "ws_disconnect",
                severity="warning",
                details={"environment": "paper", "timeframe": timeframe},
            )
        else:
            emit_lifecycle_event(
                "ws_reconnect",
                run_id=deploy_meta.run_id,
                environment="paper",
                timeframe=timeframe,
            )

    for timeframe in timeframes:
        symbols = symbols_by_timeframe.get(timeframe, [])
        if not symbols:
            continue
        feed = LiveFeed(
            event_bus=event_bus,
            clock=clock,
            ws_url=exchange_cfg.get("ws_url", "wss://stream.binancefuture.com"),
            symbols=symbols,
            timeframe=timeframe,
            rest_client=client if timeframe == primary_tf else None,
            funding_poll_interval=300,
        )
        feed.on_status_change = lambda connected, tf=timeframe: _on_ws_status(tf, connected)
        feeds.append(feed)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    user_data_healthy_once = False

    async def _stop_feeds() -> None:
        for current_feed in feeds:
            await current_feed.stop_async()

    def _halt_for_user_data_stream(reason: str, *, error: str = "") -> None:
        if stop_event.is_set():
            return
        logger.error("user_data_stream_unhealthy_halt", reason=reason, error=error)
        journal.record_runtime_event(
            "user_data_stream_unhealthy",
            severity="critical",
            details={"reason": reason, "error": error, "environment": "paper"},
        )
        emit_lifecycle_event(
            "user_data_stream_unhealthy",
            run_id=deploy_meta.run_id,
            environment="paper",
            reason=reason,
            error=error,
        )
        stop_event.set()
        loop.create_task(_stop_feeds())
        _sync_health(
            user_data_stream_ok=False,
            user_data_stream_reconnecting=False,
            user_data_stream_grace_until=None,
        )

    def _on_user_data_status(healthy: bool) -> None:
        nonlocal user_data_healthy_once
        if healthy:
            _sync_health(
                user_data_stream_ok=True,
                user_data_stream_reconnecting=False,
                user_data_stream_grace_until=None,
            )
            user_data_healthy_once = True
        else:
            _sync_health(
                user_data_stream_ok=False,
                user_data_stream_reconnecting=True,
                user_data_stream_grace_until=(
                    datetime.now(UTC) + timedelta(seconds=_USER_DATA_RECONNECT_GRACE_SEC)
                ).isoformat(),
            )

    user_data_stream = UserDataStream(
        event_bus=event_bus,
        rest_client=client,
        ws_url=exchange_cfg.get("ws_url", "wss://stream.binancefuture.com"),
        known_strategies=strategy_names,
        strategy_resolver=executor.resolve_strategy,
        on_status_change=_on_user_data_status,
    )

    def _apply_symbol_leverage() -> None:
        desired_leverage_by_symbol: dict[str, int] = {}
        for strat in strategies:
            syms = (
                strat.symbols
                if (hasattr(strat, "symbols") and isinstance(strat.symbols, list))
                else [strat.symbol]
            )
            for sym in syms:
                desired_leverage_by_symbol[sym] = max(
                    desired_leverage_by_symbol.get(sym, 1),
                    min(int(round(risk_manager.effective_max_leverage)), int(strat.leverage)),
                )

        for symbol, leverage in desired_leverage_by_symbol.items():
            try:
                client.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning("leverage_set_failed", symbol=symbol, error=str(e))

    _apply_symbol_leverage()

    def _do_reconcile() -> None:
        latency_ms = client.ping()
        kill_switch.check_api_latency(latency_ms)
        metrics.report_api_latency(latency_ms)
        reconcile_from_binance(
            client,
            journal,
            logger,
            known_strategies=strategy_names,
            strategy_resolver=executor.resolve_strategy,
        )
        _sync_health(
            api_latency_ms=round(latency_ms, 2),
            last_reconcile_ts=datetime.now(UTC).isoformat(),
        )
        journal.record_runtime_event(
            "reconcile",
            details={"api_latency_ms": round(latency_ms, 2)},
        )

    for current_feed in feeds:
        current_feed.on_reconnect = _do_reconcile
    for current_feed in feeds:
        if current_feed.timeframe == primary_tf:
            current_feed.periodic_sync_fn = _do_reconcile
    _do_reconcile()

    # ── Seed strategies with historical bars so warmup completes immediately ──
    def _seed_strategies_with_history() -> list:
        """Fetch historical klines from REST API and replay through strategies.

        Each strategy needs `warmup` bars (default 200) before generating signals.
        Without this, the bot would wait 33+ days for 200 x 4h bars to accumulate.

        Returns a list of last completed MarketEvent objects, so callers can
        trigger an immediate signal check after seeding.
        """
        from bot.core.events import MarketEvent
        from bot.utils.time_utils import ms_to_datetime

        last_bars: list = []

        for timeframe in timeframes:
            timeframe_strategies = [strategy for strategy in strategies if strategy.timeframe == timeframe]
            warmup_needed = max((getattr(strategy, "_warmup", 0) for strategy in timeframe_strategies), default=0)
            if warmup_needed == 0:
                continue

            fetch_limit = min(warmup_needed + 10, 500)
            tf_symbols = symbols_by_timeframe.get(timeframe, [])
            logger.info("seeding_historical_bars", symbols=tf_symbols, tf=timeframe, bars=fetch_limit)

            for symbol in tf_symbols:
                matching_strategies = [
                    strategy for strategy in timeframe_strategies
                    if symbol in strategy.symbols
                ]
                try:
                    klines = client.get_klines(symbol=symbol, interval=timeframe, limit=fetch_limit)
                except Exception as e:
                    logger.warning("seed_klines_fetch_failed", symbol=symbol, timeframe=timeframe, error=str(e))
                    continue

                if not klines:
                    logger.warning("seed_klines_empty", symbol=symbol, timeframe=timeframe)
                    continue

                last_event = None
                for k in klines[:-1]:
                    try:
                        event = MarketEvent(
                            timestamp=ms_to_datetime(int(k[0])),
                            symbol=symbol,
                            timeframe=timeframe,
                            open=float(k[1]),
                            high=float(k[2]),
                            low=float(k[3]),
                            close=float(k[4]),
                            volume=float(k[5]),
                            quote_volume=float(k[7]),
                            trade_count=int(k[8]),
                            taker_buy_volume=float(k[9]),
                            taker_buy_quote_volume=float(k[10]),
                            bar_timestamp=ms_to_datetime(int(k[0])),
                            source="seed",
                        )
                        last_event = event
                        for strategy in matching_strategies:
                            try:
                                strategy.on_bar(event)
                            except Exception as se:
                                logger.debug(
                                    "seed_bar_strategy_error",
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    error=str(se),
                                )
                    except Exception as e:
                        logger.debug("seed_bar_parse_error", symbol=symbol, timeframe=timeframe, error=str(e))

                if last_event is not None:
                    last_bars.append(last_event)
                bar_count = getattr(matching_strategies[0] if matching_strategies else None, "_bar_count", 0)
                logger.info(
                    "seed_complete",
                    symbol=symbol,
                    timeframe=timeframe,
                    bars_fed=len(klines) - 1,
                    bar_count=bar_count,
                )

        return last_bars

    seed_last_bars = _seed_strategies_with_history()

    def _restore_strategy_position_state() -> None:
        open_trades = journal.open_trades()
        open_states: dict[tuple[str, str], str] = {}
        if not open_trades.empty:
            for _, trade in open_trades.iterrows():
                remaining_size = float(trade.get("remaining_size", trade.get("size", 0.0)) or 0.0)
                if remaining_size <= 0:
                    continue
                direction = str(trade.get("direction", "")).lower()
                if direction not in {"long", "short"}:
                    continue
                open_states[(str(trade.get("strategy", "")), str(trade.get("symbol", "")))] = direction

        restored = 0
        for strategy in strategies:
            position_state = open_states.get((strategy.name, strategy.symbol), "flat")
            if hasattr(strategy, "restore_position_state"):
                strategy.restore_position_state(position_state)
            elif hasattr(strategy, "_in_position"):
                strategy._in_position = position_state
            if position_state != "flat":
                restored += 1

        logger.info(
            "strategy_position_state_restored",
            restored=restored,
            open_trades=len(open_states),
        )

    _restore_strategy_position_state()

    # Wire strategies to market events
    def on_market(event):
        # Allow bar processing even when user_data_stream is unavailable —
        # fill events won't arrive but signals and paper orders still work.
        if not state.get("user_data_stream_ok", False):
            _sync_health(last_bar_ts=event.timestamp.isoformat())
            # Fall through: don't return; let strategies still run

        risk_manager.set_equity(portfolio.equity)
        for strategy in strategies:
            strategy.set_equity(portfolio.equity)

        leverage_before = state.get("applied_effective_leverage")
        if event.timeframe == primary_tf:
            risk_manager.update_market_data(event.high, event.low, event.close)
            risk_manager.tick_bar()
            _report_risk_observability()
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
        elif circuit_breaker.is_tripped_at(event.timestamp):
            _sync_health(
                last_bar_ts=event.timestamp.isoformat(),
                circuit_breaker_symbol=state.get("circuit_breaker_symbol", event.symbol),
            )
            return

        for strategy in strategies:
            if event_matches_strategy(strategy, event):
                try:
                    signals = strategy.on_bar(event)
                    for sig in signals:
                        journal.record_signal_generated(
                            sig,
                            effective_max_leverage=risk_manager.effective_max_leverage,
                        )
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

        if event.timeframe == primary_tf:
            applied_effective_leverage = round(risk_manager.effective_max_leverage, 2)
            if leverage_before != applied_effective_leverage:
                _apply_symbol_leverage()
            _sync_health(
                last_bar_ts=event.timestamp.isoformat(),
                circuit_breaker_symbol="",
                applied_effective_leverage=applied_effective_leverage,
            )
        else:
            _sync_health(last_bar_ts=event.timestamp.isoformat())

    event_bus.subscribe(EventType.MARKET.value, on_market)

    def on_fill(event):
        for strategy in strategies:
            if event.strategy_name == strategy.name:
                strategy.on_fill(event)
        _sync_health()
        _report_risk_observability()

    event_bus.subscribe(EventType.FILL.value, on_fill)

    def on_reject(event):
        journal.record_runtime_event(
            "signal_rejected",
            severity="warning",
            strategy=getattr(event, "strategy_name", ""),
            symbol=getattr(event, "symbol", ""),
            details={
                "reason": getattr(event, "reason", ""),
                "source": getattr(event, "source", ""),
            },
        )

    event_bus.subscribe(EventType.REJECT.value, on_reject)

    def on_kill_switch(event):
        journal.record_runtime_event(
            "kill_switch",
            severity="critical",
            details={
                "reason": getattr(event, "reason", ""),
                "triggered_by": getattr(event, "triggered_by", ""),
                "close_all": getattr(event, "close_all", True),
            },
        )
        metrics.report_kill_switch_state(True)
        emit_lifecycle_event(
            "kill_switch",
            run_id=deploy_meta.run_id,
            environment="paper",
            reason=getattr(event, "reason", ""),
        )

    event_bus.subscribe(EventType.KILL_SWITCH.value, on_kill_switch)

    def handle_signal(*_):
        logger.info("shutdown_signal_received")
        stop_event.set()
        loop.create_task(_stop_feeds())
        loop.create_task(user_data_stream.stop())
        _sync_health(
            ws_connected=False,
            user_data_stream_ok=False,
            user_data_stream_reconnecting=False,
            user_data_stream_grace_until=None,
        )

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    async def _health_heartbeat() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(_HEALTH_HEARTBEAT_SEC)
            if stop_event.is_set():
                break
            _sync_health()

    user_data_task = asyncio.create_task(user_data_stream.start())
    # Yield control so start() can clear _stopped_event before wait_until_healthy checks it
    await asyncio.sleep(0)
    heartbeat_task = asyncio.create_task(_health_heartbeat())

    # Telegram commander — responds to /status /pos /pnl /balance /wallet /kill /help
    commander = TelegramCommander(
        notifier=notifier,
        portfolio=portfolio,
        journal=journal,
        initial_capital=args.capital,
        started_at=datetime.now(UTC),
        get_version=lambda: args.version,
        stop_event=stop_event,
        kill_switch=kill_switch,
        exchange=client,
        strategies=strategies,
        risk_manager=risk_manager,
    )
    commander_task = asyncio.create_task(commander.run())

    started = False
    run_error: BaseException | None = None
    reset_requested = False
    reset_exchange_summary = ""

    def _on_user_data_task_done(task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            _halt_for_user_data_stream("task_failed", error=str(exc))
        elif user_data_healthy_once:
            _halt_for_user_data_stream("task_stopped")

    user_data_task.add_done_callback(_on_user_data_task_done)

    async def _request_paper_reset() -> str:
        nonlocal reset_requested, reset_exchange_summary

        if stop_event.is_set():
            return "⚠️ Bot 目前正在關閉中，請稍後再查看狀態。"

        logger.warning("telegram_paper_reset_requested", triggered_by="telegram")
        journal.record_runtime_event(
            "telegram_reset_requested",
            severity="warning",
            details={"triggered_by": "telegram"},
        )

        result = await loop.run_in_executor(
            None,
            lambda: flatten_testnet_state(client, symbols=all_symbols),
        )

        if result.errors:
            logger.error("telegram_paper_reset_failed", errors=result.errors)
            return (
                "⚠️ 重置失敗，已中止重新啟動。\n"
                f"errors: {'; '.join(result.errors[:5])}"
            )

        reset_requested = True
        reset_exchange_summary = (
            f"orders_cancelled={result.canceled_orders}, "
            f"positions_closed={result.closed_positions}, "
            f"balance_before={result.balance_before}, balance_after={result.balance_after}"
        )
        emit_lifecycle_event(
            "paper_reset_requested",
            run_id=deploy_meta.run_id,
            environment="paper",
            canceled_orders=result.canceled_orders,
            closed_positions=result.closed_positions,
            balance_before=result.balance_before,
            balance_after=result.balance_after,
        )

        stop_event.set()
        await _stop_feeds()
        await user_data_stream.stop()
        _sync_health(
            ws_connected=False,
            user_data_stream_ok=False,
            user_data_stream_reconnecting=False,
            user_data_stream_grace_until=None,
        )

        balance_before = f"${result.balance_before:,.2f}" if result.balance_before is not None else "n/a"
        balance_after = f"${result.balance_after:,.2f}" if result.balance_after is not None else "n/a"
        return (
            "🔄 <b>Paper reset 已接受</b>\n"
            f"已撤單: {result.canceled_orders} 筆\n"
            f"已平倉: {result.closed_positions} 筆\n"
            f"餘額: {balance_before} → {balance_after}\n\n"
            "Bot 正在清空 paper journal 並重新啟動，之後會重新評估下單。"
        )

    commander._reset_callback = _request_paper_reset

    try:
        if not await user_data_stream.wait_until_healthy(timeout=30.0):
            logger.warning(
                "user_data_stream_unavailable",
                hint="Fill events will not be received; check API key has Futures permissions.",
            )

        if stop_event.is_set():
            return

        logger.info(
            "paper_trading_started",
            strategies=[s.name for s in strategies],
            symbols=all_symbols,
        )
        # Replay the last completed seeded bars only after the user data stream has
        # had a chance to connect so immediate startup fills are not missed.
        if seed_last_bars:
            logger.info(
                "initial_signal_trigger",
                symbols=[f"{event.symbol}:{event.timeframe}" for event in seed_last_bars],
            )
            for last_event in seed_last_bars:
                try:
                    on_market(last_event)
                except Exception as e:
                    logger.warning(
                        "initial_signal_trigger_error",
                        symbol=last_event.symbol,
                        timeframe=last_event.timeframe,
                        error=str(e),
                    )

        notifier.send_sync(
            f"🟡 PAPER trading started (testnet)\n"
            f"Version: {args.version.upper()}\n"
            f"Capital: ${args.capital} USDT\n"
            f"Strategies: {len(strategies)}\n"
            f"Coins: {', '.join(s.replace('USDT','') for s in sorted(all_symbols))}"
        )
        started = True
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*(current_feed.start_async() for current_feed in feeds))
    except Exception as e:
        run_error = e
        logger.error("paper_runtime_failed", error=str(e))
    finally:
        stop_event.set()
        await _stop_feeds()
        await user_data_stream.stop()
        heartbeat_task.cancel()
        commander_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        with suppress(asyncio.CancelledError):
            await commander_task
        try:
            await user_data_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if run_error is None:
                run_error = e
            logger.error("user_data_task_join_failed", error=str(e))

        if started:
            logger.info("paper_trading_stopped")
            if reset_requested:
                notifier.send_sync("🔄 Paper trading reset in progress, restarting bot...")
            else:
                notifier.send_sync("🔴 Paper trading stopped")

        try:
            journal.record_equity_snapshot(
                equity=portfolio.equity,
                realized_pnl=float(portfolio.equity - args.capital),
                unrealized_pnl=0.0,
                positions=[],
            )
        except Exception as e:
            logger.warning("equity_snapshot_failed", error=str(e))

        emit_lifecycle_event(
            "shutdown",
            run_id=deploy_meta.run_id,
            environment="paper",
            equity=portfolio.equity,
        )

        _sync_health(
            ws_connected=False,
            user_data_stream_ok=False,
            user_data_stream_reconnecting=False,
            user_data_stream_grace_until=None,
        )
        journal.close()
        logger.info("trade_journal_closed")

        account_snapshot: dict[str, Any] | None = None
        try:
            account_snapshot = client.get_account_info()
        except Exception as e:
            logger.warning("account_snapshot_failed", error=str(e))

        if reset_requested:
            logger.info("paper_reset_skip_review_bundle", summary=reset_exchange_summary)
            runtime_reset = clear_runtime_state(
                journal_path=journal_path,
                archive_journal=True,
                health_path=args.health_path,
            )
            logger.info(
                "paper_runtime_state_cleared",
                archived_journal=runtime_reset.archived_journal,
                cleared_health=runtime_reset.cleared_health,
                errors=runtime_reset.errors,
            )
            emit_lifecycle_event(
                "paper_reset_completed",
                run_id=deploy_meta.run_id,
                environment="paper",
                archived_journal=runtime_reset.archived_journal,
                cleared_health=runtime_reset.cleared_health,
                errors=runtime_reset.errors,
            )
        else:
            try:
                bundle_result = export_review_bundle(
                    run_dir=run_dir,
                    journal_db=journal_path,
                    health_path=args.health_path,
                    out_dir=data_dir / "review_bundles",
                    account_snapshot=account_snapshot,
                    gcs_bucket=os.environ.get("GCS_BACKUP_BUCKET", "").strip() or None,
                )
                logger.info(
                    "review_bundle_exported",
                    path=str(bundle_result.bundle_path),
                    uploaded_to_gcs=bundle_result.uploaded_to_gcs,
                )
            except Exception as e:
                logger.error("review_bundle_export_failed", error=str(e))
                if run_error is None:
                    run_error = e

        instance_guard.__exit__(None, None, None)

    if run_error is not None:
        raise run_error


if __name__ == "__main__":
    asyncio.run(main())
