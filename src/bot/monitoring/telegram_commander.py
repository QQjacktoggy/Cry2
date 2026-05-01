"""Telegram Commander — interactive command handler for the running bot.

Polls the Telegram Bot API for incoming messages and responds to commands
with live portfolio data.  Only messages from the authorised chat_id are
processed; all others are silently ignored.

Supported commands:
    /help           — Show available commands
    /status         — Overall bot status (equity, PnL, uptime, strategies)
    /pos            — Open positions (with strategy name)
    /pnl            — Profit & loss breakdown
    /balance        — Paper-trading simulated balance and equity
    /wallet         — Real Binance testnet wallet balance (from API)
    /orders         — Current open orders on exchange (with strategy info)
    /strategy       — Each strategy's current position state
    /kill CONFIRM   — Trigger emergency kill switch (requires "CONFIRM")
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from bot.exchange.binance_rest import BinanceRestClient
    from bot.monitoring.telegram_notifier import TelegramNotifier
    from bot.portfolio.portfolio import Portfolio
    from bot.portfolio.trade_journal import TradeJournal
    from bot.risk.kill_switch import KillSwitch
    from bot.risk.risk_manager import RiskManager

logger = structlog.get_logger(__name__)

_POLL_INTERVAL = 3.0          # seconds between get_updates calls
_POLL_TIMEOUT  = 2            # long-poll timeout (seconds)
_DAILY_SUMMARY_HOUR_UTC = 8   # hour (UTC) to send daily summary


class TelegramCommander:
    """Polls for Telegram messages and replies with live bot data.

    Args:
        notifier:        Existing TelegramNotifier (shares the Bot instance).
        portfolio:       Live Portfolio object.
        journal:         Live TradeJournal object.
        initial_capital: Starting capital in USDT.
        started_at:      When the bot process started.
        get_version:     Callable returning the current version string.
        stop_event:      asyncio.Event that signals the bot is shutting down.
        kill_switch:     Optional KillSwitch to allow remote triggering.
        exchange:        Optional BinanceRestClient for real wallet queries.
        reset_callback:  Optional async callback to clear paper state and restart.
    """

    def __init__(
        self,
        notifier: TelegramNotifier,
        portfolio: Portfolio,
        journal: TradeJournal,
        initial_capital: float,
        started_at: datetime | None = None,
        get_version: Callable[[], str] | None = None,
        stop_event: asyncio.Event | None = None,
        kill_switch: KillSwitch | None = None,
        exchange: BinanceRestClient | None = None,
        strategies: list | None = None,
        reset_callback: Callable[[], Awaitable[str]] | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self._notifier = notifier
        self._portfolio = portfolio
        self._journal = journal
        self._initial_capital = initial_capital
        self._started_at = started_at or datetime.now(UTC)
        self._get_version = get_version or (lambda: "unknown")
        self._stop_event = stop_event
        self._kill_switch = kill_switch
        self._exchange = exchange
        self._strategies = strategies or []
        self._reset_callback = reset_callback
        self._risk_manager = risk_manager
        self._offset: int = 0          # Telegram update offset (deduplication)
        self._authorized_chat_id = str(notifier.chat_id) if notifier else ""
        self._last_daily_summary_date: str = ""  # YYYY-MM-DD

    # ── Main loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Poll Telegram for updates and send daily summary until stop_event is set."""
        if not self._notifier or not self._notifier._bot:
            logger.warning("telegram_commander_disabled", reason="bot not initialised")
            return

        logger.info("telegram_commander_started", chat_id=self._authorized_chat_id)

        # Run polling and daily summary concurrently
        await asyncio.gather(
            self._poll_loop(),
            self._daily_summary_loop(),
        )

        logger.info("telegram_commander_stopped")

    async def _poll_loop(self) -> None:
        while True:
            if self._stop_event and self._stop_event.is_set():
                break
            try:
                await self._poll_once()
            except Exception as exc:
                logger.warning("telegram_commander_poll_error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)

    async def _daily_summary_loop(self) -> None:
        """Send a daily summary at _DAILY_SUMMARY_HOUR_UTC every day."""
        while True:
            if self._stop_event and self._stop_event.is_set():
                break
            now = datetime.now(UTC)
            today = now.strftime("%Y-%m-%d")
            if now.hour == _DAILY_SUMMARY_HOUR_UTC and today != self._last_daily_summary_date:
                self._last_daily_summary_date = today
                try:
                    summary_text = await self._cmd_status()
                    daily_header = f"🌅 <b>每日自動摘要</b> ({today})\n\n"
                    await self._send(daily_header + summary_text)
                    logger.info("telegram_commander_daily_summary_sent", date=today)
                except Exception as exc:
                    logger.warning("telegram_commander_daily_summary_error", error=str(exc))
            await asyncio.sleep(60)  # check every minute

    async def _poll_once(self) -> None:
        bot = self._notifier._bot
        updates = await bot.get_updates(
            offset=self._offset,
            timeout=_POLL_TIMEOUT,
            allowed_updates=["message"],
        )
        for update in updates:
            self._offset = update.update_id + 1
            msg = getattr(update, "message", None)
            if msg is None:
                continue
            chat_id = str(msg.chat_id)
            # Security: only respond to the authorised chat
            if self._authorized_chat_id and chat_id != self._authorized_chat_id:
                logger.warning("telegram_commander_unauthorized", from_chat=chat_id)
                continue
            text = (msg.text or "").strip()
            await self._dispatch(text, chat_id)

    async def _dispatch(self, text: str, chat_id: str) -> None:
        cmd = text.split()[0].lower().lstrip("/") if text else ""
        handlers = {
            "help":           self._cmd_help,
            "status":         self._cmd_status,
            "pos":            self._cmd_positions,
            "positions":      self._cmd_positions,
            "pnl":            self._cmd_pnl,
            "balance":        self._cmd_balance,
            "wallet":         self._cmd_wallet,
            "orders":         self._cmd_orders,
            "order":          self._cmd_orders,
            "strategy":       self._cmd_strategy,
            "strat":          self._cmd_strategy,
            "strategies":     self._cmd_strategy,
            "mode":           self._cmd_mode,
            "target_status":  self._cmd_target_status,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                reply = await handler()
            except Exception as exc:
                logger.error("telegram_commander_handler_error", cmd=cmd, error=str(exc))
                reply = f"⚠️ 指令執行錯誤: {exc}"
            await self._send(reply)
        elif cmd == "kill":
            await self._cmd_kill(text)
        elif cmd == "reset":
            await self._cmd_reset(text)
        elif cmd:
            await self._send(
                "❓ 未知指令。輸入 /help 查看可用指令。"
            )

    async def _send(self, text: str) -> None:
        await self._notifier.send_message(text)

    def _session_fills(self):
        """Return fills recorded since this commander started."""
        since = self._session_started_at_iso()
        fills = self._journal.export_fills(since=since) if since else self._journal.export_fills()
        if not fills.empty and "timestamp" in fills.columns:
            fills = fills.sort_values("timestamp")
        return fills

    def _session_started_at_iso(self) -> str | None:
        if self._started_at is None:
            return None
        started_at = self._started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        return started_at.isoformat()

    def _journal_summary(self) -> dict[str, object]:
        try:
            return dict(self._journal.summary())
        except Exception as exc:
            logger.warning("telegram_commander_journal_summary_failed", error=str(exc))
            return {}

    def _session_pnl_summary(self) -> dict[str, object]:
        """Summarize session PnL using fills since the bot started."""
        fills = self._session_fills()
        summary: dict[str, object] = {
            "session_fills": int(len(fills)),
            "session_realized_pnl": 0.0,
            "session_commission": 0.0,
            "session_net_pnl": 0.0,
            "negative_fill_count": 0,
            "negative_fills": [],
        }

        if fills.empty:
            return summary

        if "realized_pnl" in fills.columns:
            summary["session_realized_pnl"] = float(fills["realized_pnl"].sum())
            negative = fills[fills["realized_pnl"] < 0]
            summary["negative_fill_count"] = int(len(negative))
            if not negative.empty:
                fields = [
                    field
                    for field in (
                        "timestamp",
                        "symbol",
                        "side",
                        "quantity",
                        "price",
                        "realized_pnl",
                        "commission",
                        "order_id",
                        "client_order_id",
                    )
                    if field in negative.columns
                ]
                summary["negative_fills"] = (
                    negative.sort_values("timestamp", ascending=False)
                    .head(5)[fields]
                    .to_dict("records")
                )

        if "commission" in fills.columns:
            summary["session_commission"] = float(fills["commission"].sum())

        summary["session_net_pnl"] = float(summary["session_realized_pnl"]) - float(summary["session_commission"])
        return summary

    @staticmethod
    def _open_positions(portfolio: object) -> list[object]:
        """Return open positions when the portfolio exposes that API."""
        getter = getattr(portfolio, "get_open_positions", None)
        if callable(getter):
            try:
                positions = getter()
            except Exception as exc:
                logger.warning("telegram_commander_open_positions_failed", error=str(exc))
                return []
            return list(positions or [])
        return []

    @staticmethod
    def _format_negative_fill_lines(negative_fills: list[dict[str, object]]) -> list[str]:
        """Format negative fills for Telegram output."""
        if not negative_fills:
            return []

        lines = []
        for fill in negative_fills:
            timestamp = fill.get("timestamp", "?")
            symbol = fill.get("symbol", "?")
            side = fill.get("side", "?")
            quantity = float(fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("price", 0.0) or 0.0)
            realized_pnl = float(fill.get("realized_pnl", 0.0) or 0.0)
            order_id = fill.get("order_id", "")
            client_order_id = fill.get("client_order_id", "")
            order_ref = f" oid={order_id}" if order_id else ""
            client_ref = f" coid={client_order_id}" if client_order_id else ""
            lines.append(
                f"  {timestamp} {symbol} {side} qty={quantity:.3f} "
                f"price={price:.2f} pnl={realized_pnl:+.5f}{order_ref}{client_ref}"
            )
        return lines

    # ── Command handlers ───────────────────────────────────────────────────

    async def _cmd_help(self) -> str:
        lines = [
            "🤖 <b>Bot 指令列表</b>\n\n"
            "/status        — 整體狀態\n"
            "/balance       — 模擬帳戶餘額\n"
            "/wallet        — Binance 真實錢包餘額\n"
            "/pos           — 目前持倉（含策略名稱）\n"
            "/orders        — 目前掛單明細\n"
            "/strategy      — 各策略狀態\n"
            "/pnl           — 損益明細\n"
            "/mode          — 目前運作模式（積極/保守）與 regime\n"
            "/target_status — 每日目標進度\n"
        ]
        if self._reset_callback is not None:
            lines.append("/reset CONFIRM — 清除 paper 資料並重新啟動\n")
        lines.append(
            "/kill CONFIRM  — 🚨 緊急停止（需輸入 CONFIRM）\n"
            "/help          — 顯示此說明"
        )
        return "".join(lines)

    async def _cmd_status(self) -> str:
        p = self._portfolio
        uptime = _format_uptime(self._started_at)
        summary = self._session_pnl_summary()
        journal_summary = self._journal_summary()
        equity   = p.equity
        pnl_usd  = float(summary["session_realized_pnl"])
        pnl_pct  = (pnl_usd / self._initial_capital * 100) if self._initial_capital else 0.0
        pnl_emoji = "📈" if pnl_usd >= 0 else "📉"
        open_pos  = self._open_positions(p)
        version   = self._get_version()

        lines = [
            f"📊 <b>Bot 狀態</b>  [{version}]",
            f"⏱ 運行時間: {uptime}",
            "",
            f"💰 初始資金:   ${self._initial_capital:,.2f}",
            f"📈 目前權益(Paper): ${equity:,.2f}",
            f"{pnl_emoji} 啟動後已實現(未扣手續費): ${pnl_usd:+,.2f}  ({pnl_pct:+.2f}%)",
            f"💸 啟動後手續費: ${float(summary['session_commission']):,.2f}",
            f"🧾 啟動後淨盈餘: ${float(summary['session_net_pnl']):+,.2f}",
            f"📊 未實現損益: ${float(getattr(p, 'unrealized_pnl', 0.0)):+,.2f}",
            "",
            f"📋 持倉數量:   {len(open_pos)}",
            f"🔄 啟動後成交: {summary['session_fills']}",
            f"📊 完整交易:   {int(journal_summary.get('total_trades', 0) or 0)}",
            f"🎯 勝率:       {float(journal_summary.get('win_rate', 0.0) or 0.0) * 100:.1f}%",
            f"⚠️ 負PnL 成交: {summary['negative_fill_count']}",
        ]
        lines.extend(self._format_negative_fill_lines(summary["negative_fills"]))
        return "\n".join(lines)

    async def _cmd_balance(self) -> str:
        p = self._portfolio
        equity      = p.equity
        cash        = p.cash
        unrealized  = p.unrealized_pnl
        realized    = p.realized_pnl
        pnl_total   = equity - self._initial_capital
        pnl_pct     = (pnl_total / self._initial_capital * 100) if self._initial_capital else 0.0

        lines = [
            "💰 <b>帳戶餘額</b>",
            "",
            f"現金餘額:       ${cash:,.2f} USDT",
            f"未實現損益:     ${unrealized:+,.2f} USDT",
            f"總權益:         ${equity:,.2f} USDT",
            "",
            f"已實現損益:     ${realized:+,.2f} USDT",
            f"總報酬:         ${pnl_total:+,.2f} ({pnl_pct:+.2f}%)",
        ]
        return "\n".join(lines)

    async def _cmd_positions(self) -> str:
        open_pos = self._portfolio.get_open_positions()
        if not open_pos:
            return "📋 <b>目前無持倉</b>"

        # Build a map from symbol to strategy names currently in position
        strategy_map: dict[str, list[str]] = {}
        for s in self._strategies:
            sym = getattr(s, "symbol", None) or (list(getattr(s, "symbols", [None]))[0] if getattr(s, "symbols", []) else None)
            in_pos = getattr(s, "_in_position", "flat")
            if sym and in_pos != "flat":
                strategy_map.setdefault(sym, []).append(s.name)

        lines = [f"📋 <b>持倉明細</b> ({len(open_pos)} 個)\n"]
        for pos in open_pos:
            side_emoji = "🟢" if pos.side.value == "LONG" else "🔴"
            pnl_emoji  = "📈" if pos.unrealized_pnl >= 0 else "📉"
            strats = strategy_map.get(pos.symbol, [])
            strat_str = f"\n   📌 策略: {', '.join(strats)}" if strats else ""
            lines.append(
                f"{side_emoji} <b>{pos.symbol}</b> {pos.side.value}{strat_str}\n"
                f"   數量: {pos.quantity:.4f}  均價: ${pos.entry_price:,.4f}\n"
                f"   現價: ${pos.current_price:,.4f}\n"
                f"   {pnl_emoji} 未實現: ${pos.unrealized_pnl:+,.2f} USDT"
            )
        return "\n\n".join(lines)

    async def _cmd_pnl(self) -> str:
        summary  = self._session_pnl_summary()
        journal_summary = self._journal_summary()
        p        = self._portfolio
        net_pnl  = float(summary["session_realized_pnl"])
        fees     = float(summary["session_commission"])
        trades   = int(summary["session_fills"])
        completed_trades = int(journal_summary.get("total_trades", 0) or 0)
        win_rate = float(journal_summary.get("win_rate", 0.0) or 0.0)
        unrealized = p.unrealized_pnl
        total_pnl  = float(summary["session_net_pnl"]) + unrealized
        pnl_pct    = (total_pnl / self._initial_capital * 100) if self._initial_capital else 0.0

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        lines = [
            f"{pnl_emoji} <b>損益明細</b>",
            "",
            f"啟動後已實現(未扣手續費): ${net_pnl:+,.2f} USDT",
            f"未實現損益:   ${unrealized:+,.2f} USDT",
            f"啟動後淨盈餘: ${total_pnl:+,.2f} USDT  ({pnl_pct:+.2f}%)",
            "",
            f"手續費支出:   ${fees:,.2f} USDT",
            f"啟動後成交數: {trades}",
            f"完整交易數:   {completed_trades}",
            f"勝率:         {win_rate * 100:.1f}%",
        ]

        # Per-symbol breakdown from journal
        try:
            fills = self._session_fills()
            if not fills.empty and "symbol" in fills.columns:
                sym_pnl = (
                    fills.groupby("symbol")["realized_pnl"]
                    .sum()
                    .sort_values(ascending=False)
                )
                if not sym_pnl.empty:
                    lines.append("\n<b>各幣種損益:</b>")
                    for sym, val in sym_pnl.items():
                        e = "📈" if val >= 0 else "📉"
                        lines.append(f"  {e} {sym}: ${val:+,.2f}")
                negative_lines = self._format_negative_fill_lines(summary["negative_fills"])
                if negative_lines:
                    lines.append("\n<b>最近負PnL成交:</b>")
                    lines.extend(negative_lines)
        except Exception:
            pass

        return "\n".join(lines)

    async def _cmd_orders(self) -> str:
        """Query open orders from Binance exchange with strategy info."""
        if self._exchange is None:
            return "⚠️ 未連接交易所，無法查詢掛單。"

        try:
            loop = asyncio.get_event_loop()
            all_orders = []
            # Query open orders for each symbol in use
            symbols_in_use = {getattr(s, "symbol", None) for s in self._strategies}
            symbols_in_use.discard(None)
            if not symbols_in_use:
                return "📋 <b>目前無掛單</b>\n\n（無監控的交易對）"

            for sym in symbols_in_use:
                try:
                    orders = await loop.run_in_executor(
                        None,
                        lambda s=sym: self._exchange._client.futures_get_open_orders(symbol=s)
                        if self._exchange._client else []
                    )
                    for o in orders:
                        o["_symbol"] = sym
                    all_orders.extend(orders)
                except Exception:
                    pass
        except Exception as exc:
            return f"❌ 查詢掛單失敗: {exc}"

        if not all_orders:
            return "📋 <b>目前無掛單</b>"

        # Build client_order_id → strategy name map
        coid_to_strategy: dict[str, str] = {}
        for s in self._strategies:
            # strategies store order IDs in _open_orders if any
            open_ords = getattr(s, "_open_orders", {})
            for oid in open_ords:
                coid_to_strategy[str(oid)] = s.name

        lines = [f"📋 <b>掛單明細</b> ({len(all_orders)} 筆)\n"]
        for o in all_orders:
            sym = o.get("symbol", o.get("_symbol", "?"))
            side = o.get("side", "?")
            otype = o.get("type", "?")
            qty = float(o.get("origQty", 0))
            price = float(o.get("price", 0))
            stop_price = float(o.get("stopPrice", 0))
            coid = o.get("clientOrderId", "")
            oid = str(o.get("orderId", ""))
            strat = coid_to_strategy.get(coid) or coid_to_strategy.get(oid) or "—"
            side_emoji = "🟢" if side == "BUY" else "🔴"
            price_str = f"@${price:,.4f}" if price > 0 else ""
            stop_str = f"  止損:${stop_price:,.4f}" if stop_price > 0 else ""
            lines.append(
                f"{side_emoji} <b>{sym}</b> {side} {otype}\n"
                f"   數量: {qty:.4f} {price_str}{stop_str}\n"
                f"   📌 策略: {strat}\n"
                f"   OrderID: {oid}"
            )
        return "\n\n".join(lines)

    async def _cmd_strategy(self) -> str:
        """Show each strategy's current position state."""
        if not self._strategies:
            return "⚠️ 無策略資訊。"

        lines = ["📊 <b>策略狀態</b>\n"]
        for s in self._strategies:
            name = s.name
            in_pos = getattr(s, "_in_position", "?")
            sym = getattr(s, "symbol", None) or (list(getattr(s, "symbols", ["?"]))[0])
            bar_count = getattr(s, "_bar_count", 0)
            alloc = getattr(s, "_allocation_usd", 0)
            leverage = getattr(s, "leverage", 1)
            if in_pos == "flat":
                state_emoji = "⬜"
            elif in_pos == "long":
                state_emoji = "🟢"
            elif in_pos == "short":
                state_emoji = "🔴"
            else:
                state_emoji = "❓"
            lines.append(
                f"{state_emoji} <code>{name}</code>\n"
                f"   {sym}  倉位: {in_pos}  配額: ${alloc:.1f} x{leverage}\n"
                f"   Bar數: {bar_count}"
            )
        return "\n\n".join(lines)

    async def _cmd_mode(self) -> str:
        """Report current aggressive/conservative mode and market regime."""
        if self._risk_manager is None:
            return "⚠️ RiskManager 未連接，無法查詢模式。"

        rm = self._risk_manager
        conservative = rm.conservative_mode
        session_summary = self._session_pnl_summary()
        daily_pnl = float(session_summary["session_realized_pnl"])
        target = getattr(rm, "_daily_profit_target_usd", 0.0)
        regime = rm.regime.value if hasattr(rm.regime, "value") else str(rm.regime)
        adx = rm.adx

        mode_emoji = "🐢" if conservative else "🚀"
        mode_text = "保守模式 (Conservative)" if conservative else "積極模式 (Aggressive)"

        lines = [
            f"{mode_emoji} <b>運作模式</b>",
            "",
            f"模式:       {mode_text}",
            f"市場 Regime: {regime.upper()}  (ADX={adx:.1f})",
            f"日內損益:   ${daily_pnl:+,.4f} USDT",
        ]
        if target > 0:
            remaining = max(target - daily_pnl, 0.0)
            lines.append(f"日標:       ${target:.2f}  (尚差 ${remaining:.2f})")
        return "\n".join(lines)

    async def _cmd_target_status(self) -> str:
        """Report daily profit target progress."""
        if self._risk_manager is None:
            return "⚠️ RiskManager 未連接，無法查詢目標進度。"

        rm = self._risk_manager
        daily_pnl = float(self._session_pnl_summary()["session_realized_pnl"])
        target = getattr(rm, "_daily_profit_target_usd", 0.0)
        conservative = rm.conservative_mode

        if target <= 0:
            return (
                "ℹ️ <b>每日目標進度</b>\n\n"
                "daily_profit_target_usd = 0，目標功能未啟用。"
            )

        pct = min(daily_pnl / target * 100, 100.0) if target > 0 else 0.0
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        now = datetime.now(UTC)
        # Next UTC midnight
        from datetime import timedelta
        next_reset = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        until_reset = next_reset - now
        hours, rem = divmod(int(until_reset.total_seconds()), 3600)
        minutes = rem // 60

        status_emoji = "✅" if conservative else "⏳"
        lines = [
            f"{status_emoji} <b>每日目標進度</b>",
            "",
            f"[{bar}] {pct:.1f}%",
            f"日內損益: ${daily_pnl:+,.4f} / ${target:.2f} USDT",
            "",
            f"狀態: {'🐢 保守模式（目標達成）' if conservative else '🚀 積極模式（追目標中）'}",
            f"UTC 重置: {hours}h {minutes}m 後",
        ]
        return "\n".join(lines)

    async def _cmd_kill(self, text: str) -> None:
        """Handle /kill command — requires CONFIRM keyword."""
        parts = text.split()
        confirmed = len(parts) >= 2 and parts[1].upper() == "CONFIRM"

        if not confirmed:
            await self._send(
                "🚨 <b>緊急停止</b>\n\n"
                "此操作將觸發 Kill Switch，停止所有交易並嘗試平倉。\n\n"
                "確認請發送：\n<code>/kill CONFIRM</code>"
            )
            return

        if self._kill_switch is None:
            await self._send("⚠️ Kill Switch 未初始化，無法遠端觸發。")
            return

        if self._kill_switch.is_triggered:
            await self._send("⚠️ Kill Switch 已經處於觸發狀態。")
            return

        logger.critical("telegram_commander_kill_switch_triggered", triggered_by="telegram")
        self._kill_switch.trigger("Telegram 遠端指令", triggered_by="telegram")
        await self._send(
            "🚨 <b>Kill Switch 已觸發！</b>\n"
            "Bot 正在停止交易並嘗試平倉所有部位。"
        )

    async def _cmd_reset(self, text: str) -> None:
        """Handle /reset command — requires CONFIRM keyword."""
        if self._reset_callback is None:
            await self._send("⚠️ 目前執行環境不支援 /reset。")
            return

        parts = text.split()
        confirmed = len(parts) >= 2 and parts[1].upper() == "CONFIRM"

        if not confirmed:
            await self._send(
                "🔄 <b>重置並重新啟動</b>\n\n"
                "此操作會撤銷目前掛單、平掉 testnet 倉位、清空 paper journal，"
                "然後重新啟動 bot 並重新評估下單。\n\n"
                "確認請發送：\n<code>/reset CONFIRM</code>"
            )
            return

        try:
            reply = await self._reset_callback()
        except Exception as exc:
            logger.error("telegram_commander_reset_error", error=str(exc))
            reply = f"⚠️ 重置失敗: {exc}"
        await self._send(reply)

    async def _cmd_wallet(self) -> str:
        """Query real Binance testnet wallet balance via REST API."""
        if self._exchange is None:
            return "⚠️ 未連接交易所，無法查詢真實錢包餘額。"

        try:
            loop = asyncio.get_event_loop()
            account = await loop.run_in_executor(
                None, self._exchange.get_account_info
            )
        except Exception as exc:
            logger.warning("telegram_commander_wallet_query_failed", error=str(exc))
            return f"❌ 查詢錢包失敗: {exc}"

        assets = account.get("assets", [])
        # Filter assets with nonzero balance
        nonzero = [
            a for a in assets
            if float(a.get("walletBalance", 0)) > 0
        ]
        if not nonzero:
            return "💳 <b>Binance 錢包</b>\n\n所有資產餘額為零。"

        lines = ["💳 <b>Binance Testnet 錢包</b>\n"]
        total_usd = 0.0
        for a in sorted(nonzero, key=lambda x: -float(x.get("walletBalance", 0))):
            asset      = a.get("asset", "?")
            wallet_bal = float(a.get("walletBalance", 0))
            avail_bal  = float(a.get("availableBalance", 0))
            unreal_pnl = float(a.get("unrealizedProfit", 0))
            total_usd += wallet_bal  # only meaningful for USDT in testnet
            pnl_str = f"  未實現: {unreal_pnl:+,.2f}" if abs(unreal_pnl) > 0.001 else ""
            lines.append(
                f"<b>{asset}</b>: {wallet_bal:,.4f}"
                f"  (可用: {avail_bal:,.4f}){pnl_str}"
            )

        # Summarise overall equity from account
        total_equity = float(account.get("totalWalletBalance", 0))
        total_margin = float(account.get("totalInitialMargin", 0))
        total_unreal = float(account.get("totalUnrealizedProfit", 0))
        lines.append(
            f"\n<b>總錢包餘額:</b>  ${total_equity:,.2f} USDT"
            f"\n<b>使用中保證金:</b> ${total_margin:,.2f} USDT"
            f"\n<b>未實現總損益:</b> ${total_unreal:+,.2f} USDT"
        )
        return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────

def _format_uptime(started_at: datetime) -> str:
    delta = datetime.now(UTC) - started_at
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m {seconds}s"
