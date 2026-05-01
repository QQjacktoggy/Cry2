"""Jackbot Commander — interactive command handler for the running bot."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from jackbot.notify.telegram import TelegramBot
    from jackbot.portfolio.exchange_journal import ExchangeJournal
    from jackbot.portfolio.portfolio import Portfolio
    from jackbot.strategy.day_trader import DayTrader

logger = structlog.get_logger(__name__)

_POLL_INTERVAL = 5.0
_POLL_TIMEOUT = 15


class JackbotCommander:
    def __init__(
        self,
        bot: TelegramBot,
        trader: DayTrader,
        portfolio: Portfolio,
        client: Any,
        stop_event: asyncio.Event,
        journal: ExchangeJournal | None = None,
        started_at: datetime | None = None,
        status_provider: Any | None = None,
        reconcile_callback: Any | None = None,
        safe_mode_callback: Any | None = None,
        repair_callback: Any | None = None,
    ) -> None:
        self._bot = bot
        self._trader = trader
        self._portfolio = portfolio
        self._client_api = client
        self._journal = journal
        self._started_at = self._normalize_started_at(started_at)
        self._status_provider = status_provider
        self._reconcile_callback = reconcile_callback
        self._safe_mode_callback = safe_mode_callback
        self._repair_callback = repair_callback
        self._stop_event = stop_event
        self._offset: int = 0
        raw_chat_id = getattr(bot, "_chat_id", "")
        self._authorized_chat_id = "" if raw_chat_id in (None, "", "None") else str(raw_chat_id)
        self._authorization_enabled = bool(self._authorized_chat_id)
        self._client = httpx.AsyncClient(timeout=_POLL_TIMEOUT + 5.0)
        self._backoff_until = 0.0

    async def run(self) -> None:
        if not self._bot or not self._bot._enabled:
            return
        if not self._authorization_enabled:
            logger.error("commander_disabled_missing_chat_id")
            return
        logger.info("commander_started", chat_id=self._authorized_chat_id)
        while not self._stop_event.is_set():
            now = asyncio.get_event_loop().time()
            if now < self._backoff_until:
                await asyncio.sleep(1)
                continue

            try:
                await self._poll_once()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("commander_rate_limited", retry_after=60)
                    self._backoff_until = now + 60
                else:
                    logger.warning("commander_http_error", status=exc.response.status_code)
            except Exception as exc:
                logger.warning("commander_poll_error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)
        logger.info("commander_stopped")
        await self._client.aclose()

    async def _poll_once(self) -> None:
        url = f"https://api.telegram.org/bot{self._bot._token}/getUpdates"
        params = {"offset": self._offset, "timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]}
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return
        updates = data.get("result", [])

        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue

            chat_id = str(msg.get("chat", {}).get("id", ""))
            if not self._authorization_enabled:
                continue
            if chat_id != self._authorized_chat_id:
                continue

            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                await self._dispatch(text, chat_id)

    async def _dispatch(self, text: str, chat_id: str) -> None:
        parts = text.split()
        cmd = parts[0].lower().lstrip("/")

        handlers = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "balance": self._cmd_balance,
            "pos": self._cmd_pos,
            "pnl": self._cmd_pnl,
            "wallet": self._cmd_wallet,
            "orders": self._cmd_orders,
            "grids": self._cmd_grids,
            "strategy": self._cmd_strategy,
            "risk": self._cmd_risk,
            "health": self._cmd_health,
            "fills": self._cmd_fills,
            "reconcile": self._cmd_reconcile,
            "repair": self._cmd_repair,
            "safe": self._cmd_safe,
            "pause": self._cmd_safe,
            "halt": self._cmd_safe,
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                reply = await handler(parts[1:])
                self._bot.send(reply)
            except Exception as e:
                logger.error("commander_handler_error", cmd=cmd, error=str(e))
                self._bot.send(f"⚠️ 指令執行失敗: {e}")
        elif cmd:
            self._bot.send("❓ 未知指令。輸入 /help 查看可用指令。")

    @staticmethod
    def _normalize_started_at(started_at: datetime | None) -> datetime:
        if started_at is None:
            return datetime.now(UTC)
        if started_at.tzinfo is None:
            return started_at.replace(tzinfo=UTC)
        return started_at

    def _session_started_at_iso(self) -> str:
        return self._started_at.isoformat()

    def _session_fills(self) -> list[dict[str, object]]:
        """Return fills recorded since the commander started."""
        if self._journal is None:
            return []
        since = self._session_started_at_iso()
        if since and hasattr(self._journal, "fills_since"):
            fills = self._journal.fills_since(since)
        else:
            fills = self._journal.recent_fills(limit=200)
        fills = list(fills or [])
        fills.sort(key=lambda item: str(item.get("timestamp", "")))
        return fills

    def _session_summary(self) -> dict[str, object]:
        """Summarize session PnL using fills since the bot started."""
        fills = self._session_fills()
        if self._journal is None:
            return {
                "session_fills": len(fills),
                "session_realized_pnl": 0.0,
                "session_commission": 0.0,
                "session_net_pnl": 0.0,
                "session_last_fill_at": None,
                "negative_fill_count": 0,
                "negative_fills": [],
            }

        if self._started_at and hasattr(self._journal, "get_summary_since"):
            summary = self._journal.get_summary_since(self._session_started_at_iso())
        else:
            summary = self._journal.get_summary()

        negative_fills = [fill for fill in fills if float(fill.get("realized_pnl", 0.0) or 0.0) < 0]
        negative_fills = negative_fills[-5:]
        realized = float(getattr(summary, "today_realized_pnl", 0.0) or 0.0)
        commission = float(getattr(summary, "today_commission", 0.0) or 0.0)
        return {
            "session_fills": int(getattr(summary, "today_fills", len(fills)) or 0),
            "session_realized_pnl": realized,
            "session_commission": commission,
            "session_net_pnl": realized - commission,
            "session_last_fill_at": getattr(summary, "last_fill_at", None),
            "negative_fill_count": len(negative_fills),
            "negative_fills": negative_fills,
        }

    def _exchange_summary_from_status(self, status: dict[str, Any]) -> dict[str, object]:
        """Return exchange-first PnL/trade numbers for Telegram commands."""
        session_summary = self._session_summary()
        realized = float(
            status.get(
                "exchange_today_realized_pnl",
                status.get("session_realized_pnl", session_summary["session_realized_pnl"]),
            )
            or 0.0
        )
        commission = float(
            status.get(
                "exchange_today_commission",
                status.get("session_commission", session_summary["session_commission"]),
            )
            or 0.0
        )
        fills = int(
            status.get(
                "exchange_today_fills",
                status.get("session_fills", session_summary["session_fills"]),
            )
            or 0
        )
        negative_fills = list(status.get("session_negative_fills", session_summary["negative_fills"]) or [])
        return {
            "realized_pnl": realized,
            "commission": commission,
            "net_pnl": realized - commission,
            "fills": fills,
            "last_fill_at": status.get("exchange_last_fill_at", session_summary["session_last_fill_at"]),
            "negative_fill_count": int(
                status.get("session_negative_fill_count", session_summary["negative_fill_count"]) or 0
            ),
            "negative_fills": negative_fills,
        }

    @staticmethod
    def _format_negative_fill_lines(negative_fills: list[dict[str, object]]) -> list[str]:
        """Format negative fills for Telegram output."""
        if not negative_fills:
            return ["負PnL 成交: 無"]

        lines = [f"負PnL 成交: {len(negative_fills)} 筆（最近 5 筆）"]
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

    async def _cmd_help(self, args: list[str] | None = None) -> str:
        return (
            "🤖 <b>Jackbot 指令列表</b>\n\n"
            "/status   — 網格狀態與持倉\n"
            "/strategy — 策略、模式、regime\n"
            "/grids    — 網格 level 狀態\n"
            "/balance  — 帳戶權益與盈虧\n"
            "/pnl      — 詳細損益明細\n"
            "/wallet   — 真實錢包餘額 (Binance)\n"
            "/orders   — 當前交易所掛單\n"
            "/pos      — 真實交易所倉位\n"
            "/fills 10 — 最近成交\n"
            "/risk     — 風控與 safe mode\n"
            "/health   — reconcile/WS/DB 狀態\n"
            "/reconcile — 重新比對交易所狀態\n"
            "/repair   — 產生修復 dry-run 計畫\n"
            "/repair confirm — 執行可安全修復的補單\n"
            "/safe     — 暫停開新單，只保留監控\n"
            "/help     — 顯示此幫助"
        )

    async def _cmd_status(self, args: list[str] | None = None) -> str:
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        exchange_summary = self._exchange_summary_from_status(status)
        active = status.get("active_grids", [])
        repair = status.get("exchange_repair_plan", {})
        portfolio_summary = self._portfolio.get_summary()
        started_label = self._started_at.strftime("%m-%d %H:%M UTC")

        lines = ["📊 <b>Jackbot 狀態</b>"]
        lines.append(f"• 策略: {status.get('strategy_variant', '?')}")
        lines.append(f"• 模式: {status.get('mode', '?')}")
        lines.append(f"• Safe mode: {status.get('safe_mode', False)} {status.get('safe_mode_reason', '')}")
        lines.append(f"⚡ <i>PnL 起算: {started_label} (Docker 啟動)</i>")
        lines.append(f"• 交易所已實現(未扣手續費): ${float(exchange_summary['realized_pnl']):+.4f} USDT")
        lines.append(f"• 交易所手續費: ${float(exchange_summary['commission']):.6f} USDT")
        lines.append(f"• 交易所淨盈餘: ${float(exchange_summary['net_pnl']):+.4f} USDT")
        lines.append(f"• Paper 權益: <b>${portfolio_summary['total_equity']:.2f}</b>")
        unrealized = float(portfolio_summary.get("unrealized_pnl", 0.0))
        lines.append(f"• 未實現損益: ${unrealized:+.4f} USDT")
        lines.append(f"• 活躍網格: {len(active)} 個")
        lines.append(f"• 交易所掛單: {len(status.get('exchange_open_orders', []))} 筆")
        lines.append(f"• Orphan 掛單: {len(status.get('exchange_orphan_orders', []))} 筆")
        lines.append(f"• Orphan 倉位: {len(status.get('exchange_orphan_positions', []))} 筆")
        lines.append(f"• Recoverable 修復: {len(repair.get('recoverable_actions', []))} 筆")
        lines.append(f"• Blocking 問題: {len(repair.get('blocking_issues', []))} 筆")
        lines.append(f"• 交易所成交筆數: {exchange_summary['fills']} 筆")

        for g in active:
            lines.append(f"\n🏷 <b>{g['symbol']} ({g['direction']})</b>")
            lines.append(f"  - 範圍: {g['range']}")
            lines.append(f"  - 槓桿: {g['leverage']}x")
            lines.append(f"  - 浮盈: ${g['unrealized_pnl']:.2f}")

            level_details = g.get("level_details", [])
            if level_details:
                pending = [level for level in level_details if "pending" in level["state"]]
                filled = [level for level in level_details if "filled" in level["state"]]
                lines.append(f"  - 狀態: {len(filled)} 已成交, {len(pending)} 等待中")

        lines.extend(self._format_negative_fill_lines(exchange_summary["negative_fills"]))
        return "\n".join(lines)

    async def _cmd_pnl(self, args: list[str] | None = None) -> str:
        fills = self._session_fills()
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        exchange_summary = self._exchange_summary_from_status(status)
        active = status.get("active_grids", [])
        portfolio_summary = self._portfolio.get_summary()
        started_label = self._started_at.strftime("%Y-%m-%d %H:%M UTC")

        lines = ["📈 <b>損益明細</b>"]
        lines.append(f"⚡ <i>起算: {started_label} (Docker 啟動時間)</i>\n")
        lines.append(f"• 交易所已實現(未扣手續費): ${float(exchange_summary['realized_pnl']):+.4f} USDT")
        lines.append(f"• 交易所手續費: ${float(exchange_summary['commission']):.6f} USDT")
        lines.append(f"• 交易所淨盈餘: ${float(exchange_summary['net_pnl']):+.4f} USDT")
        unrealized = float(portfolio_summary.get("unrealized_pnl", 0.0))
        lines.append(f"• 未實現損益: ${unrealized:+.4f} USDT")
        lines.append(f"• 交易所成交筆數: {exchange_summary['fills']} 次")

        if active:
            lines.append(f"\n<b>活躍網格:</b> {len(active)} 個")
            for g in active:
                lines.append(f"  · {g['symbol']} {g['direction']} 浮盈 ${g['unrealized_pnl']:.2f}")

        if fills and "symbol" in fills[0] and "realized_pnl" in fills[0]:
            lines.append("\n<b>幣種表現:</b>")
            sym_pnl: dict[str, float] = {}
            for fill in fills:
                sym = str(fill.get("symbol", "?"))
                sym_pnl[sym] = sym_pnl.get(sym, 0.0) + float(fill.get("realized_pnl", 0.0) or 0.0)
            for sym, pnl in sorted(sym_pnl.items(), key=lambda item: -item[1]):
                emoji = "📈" if pnl >= 0 else "📉"
                lines.append(f"  {emoji} {sym}: ${pnl:+.4f}")

        lines.extend(self._format_negative_fill_lines(exchange_summary["negative_fills"]))
        return "\n".join(lines)

    async def _cmd_balance(self, args: list[str] | None = None) -> str:
        summary = self._portfolio.get_summary()
        return (
            "💰 <b>帳戶權益資訊 (虛擬)</b>\n\n"
            f"• 初始資金: ${summary['initial_capital']:.2f}\n"
            f"• 總獲利: ${summary['realized_pnl']:.4f}\n"
            f"• 未實現: ${summary['unrealized_pnl']:+.4f}\n"
            f"<b>• 當前權益: ${summary['total_equity']:.2f}</b>\n"
            f"• 今日盈虧: ${summary['today_pnl']:+.4f}\n"
            f"• 模式: {self._trader.mode.value.upper()}"
        )

    async def _cmd_wallet(self, args: list[str] | None = None) -> str:
        """Query real Binance balance."""
        loop = asyncio.get_event_loop()
        account = await loop.run_in_executor(None, self._client_api.get_account_info)

        lines = ["💳 <b>Binance 錢包餘額</b>\n"]
        assets = [a for a in account.get("assets", []) if float(a.get("walletBalance", 0)) > 0.01]

        for a in assets:
            lines.append(f"• <b>{a['asset']}</b>: {float(a['walletBalance']):.4f}")

        lines.append(f"\n<b>總權益:</b> ${float(account.get('totalMarginBalance', 0)):.2f} USDT")
        lines.append(f"<b>可用保證金:</b> ${float(account.get('availableBalance', 0)):.2f} USDT")
        return "\n".join(lines)

    async def _cmd_orders(self, args: list[str] | None = None) -> str:
        """Query open orders."""
        lines = ["📋 <b>當前掛單明細</b>"]
        loop = asyncio.get_event_loop()

        any_order = False
        for symbol in self._trader._cfg.symbols:
            orders = await loop.run_in_executor(None, self._client_api.get_open_orders, symbol)
            if orders:
                any_order = True
                lines.append(f"\n🏷 <b>{symbol}</b>")
                for o in orders:
                    side = "買" if o["side"] == "BUY" else "賣"
                    lines.append(f"  - {side} {o['origQty']} @ {o['price']}")

        if not any_order:
            return "📋 <b>目前沒有任何掛單</b>"

        return "\n".join(lines)

    async def _cmd_pos(self, args: list[str] | None = None) -> str:
        lines = ["📍 <b>交易所倉位</b>"]
        loop = asyncio.get_event_loop()
        any_position = False
        for symbol in self._trader._cfg.symbols:
            position = await loop.run_in_executor(None, self._client_api.get_position, symbol)
            qty = float(position.get("positionAmt", 0) or 0)
            if abs(qty) <= 0:
                continue
            any_position = True
            lines.append(
                f"\n<b>{symbol}</b> qty={qty:+.6f}\n"
                f"Entry: {float(position.get('entryPrice', 0) or 0):.4f}\n"
                f"Mark: {float(position.get('markPrice', 0) or 0):.4f}\n"
                f"Unrealized: ${float(position.get('unRealizedProfit', 0) or 0):+.4f}\n"
                f"Lev: {position.get('leverage', '?')}x {position.get('marginType', '')}"
            )
        if not any_position:
            return "📍 <b>目前沒有交易所倉位</b>"
        return "\n".join(lines)

    async def _cmd_grids(self, args: list[str] | None = None) -> str:
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        active = status.get("active_grids", [])
        if not active:
            return "📐 <b>目前沒有 active grid</b>"
        lines = ["📐 <b>Grid 明細</b>"]
        for grid in active:
            lines.append(
                f"\n<b>{grid['grid_id']}</b> {grid['symbol']} {grid['direction']} "
                f"{grid['range']} PnL ${grid.get('net_pnl', 0):+.4f}"
            )
            for idx, level in enumerate(grid.get("level_details", [])[:12]):
                lines.append(
                    f"  L{idx} {level.get('price')} {level.get('state')} "
                    f"B:{level.get('buy_order') or '-'} S:{level.get('sell_order') or '-'}"
                )
        return "\n".join(lines)

    async def _cmd_strategy(self, args: list[str] | None = None) -> str:
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        regimes = status.get("latest_variant_regime", {})
        pnl = status.get("strategy_pnl", {})
        return (
            "🧭 <b>策略狀態</b>\n"
            f"Variant: <code>{status.get('strategy_variant', '?')}</code>\n"
            f"Mode: <code>{status.get('mode', '?')}</code>\n"
            f"Safe mode: <code>{status.get('safe_mode', False)}</code>\n"
            f"Daily target: ${float(status.get('daily_target', 0)):.2f}\n"
            f"Daily loss: ${float(status.get('daily_loss', 0)):+.4f}\n"
            f"Regime: <code>{regimes}</code>\n"
            f"Strategy PnL: <code>{pnl}</code>"
        )

    async def _cmd_risk(self, args: list[str] | None = None) -> str:
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        return (
            "🛡️ <b>風控狀態</b>\n"
            f"Halted: <code>{status.get('halted', False)}</code>\n"
            f"Safe mode: <code>{status.get('safe_mode', False)}</code>\n"
            f"Reason: <code>{status.get('safe_mode_reason') or '-'}</code>\n"
            f"Daily loss: ${float(status.get('daily_loss', 0)):+.4f}\n"
            f"Daily resets: {status.get('daily_resets', 0)}\n"
            f"Orphan orders: {len(status.get('exchange_orphan_orders', []))}\n"
            f"Orphan positions: {len(status.get('exchange_orphan_positions', []))}"
        )

    async def _cmd_health(self, args: list[str] | None = None) -> str:
        status = self._status_provider() if self._status_provider else self._trader.get_status()
        reconcile = status.get("exchange_reconcile", {})
        repair = status.get("exchange_repair_plan", {})
        return (
            "🩺 <b>Health</b>\n"
            f"Reconcile: <code>{reconcile.get('status', 'not_run')}</code>\n"
            f"Repair plan: <code>{repair.get('status', 'not_run')}</code>\n"
            f"Open orders: {len(reconcile.get('open_orders', []))}\n"
            f"Orphan orders: {len(reconcile.get('orphan_orders', []))}\n"
            f"Orphan positions: {len(reconcile.get('orphan_positions', []))}\n"
            f"Recoverable actions: {len(repair.get('recoverable_actions', []))}\n"
            f"Blocking issues: {len(repair.get('blocking_issues', []))}\n"
            f"Last fill: {status.get('session_last_fill_at') or '-'}"
        )

    async def _cmd_fills(self, args: list[str] | None = None) -> str:
        limit = 10
        if args:
            try:
                limit = max(1, min(30, int(args[0])))
            except ValueError:
                limit = 10
        fills = (self._journal.recent_fills(limit) if self._journal else self._session_fills()[-limit:])
        if not fills:
            return "📜 <b>目前沒有成交紀錄</b>"
        lines = [f"📜 <b>最近 {len(fills)} 筆成交</b>"]
        for fill in fills:
            net = float(fill.get("realized_pnl", 0.0) or 0.0) - float(fill.get("commission", 0.0) or 0.0)
            lines.append(
                f"{fill.get('timestamp', '?')} {fill.get('symbol', '?')} {fill.get('side', '?')} "
                f"{float(fill.get('quantity', 0.0) or 0.0):.4f}@{float(fill.get('price', 0.0) or 0.0):.2f} "
                f"net={net:+.5f} oid={fill.get('order_id', '-')}"
            )
        return "\n".join(lines)

    async def _cmd_reconcile(self, args: list[str] | None = None) -> str:
        if self._reconcile_callback is None:
            return "⚠️ Reconcile callback 尚未接上"
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, self._reconcile_callback)
        return (
            "🔎 <b>Reconcile 完成</b>\n"
            f"Status: <code>{report.get('status')}</code>\n"
            f"Open orders: {len(report.get('open_orders', []))}\n"
            f"Orphan orders: {len(report.get('orphan_orders', []))}\n"
            f"Orphan positions: {len(report.get('orphan_positions', []))}"
        )

    async def _cmd_repair(self, args: list[str] | None = None) -> str:
        if self._repair_callback is None:
            return "⚠️ Repair callback 尚未接上"
        mode = (args[0].lower() if args else "dryrun")
        if mode not in {"dryrun", "plan", "confirm", "apply"}:
            return "🧰 用法: /repair dryrun 或 /repair confirm"

        loop = asyncio.get_event_loop()
        plan = await loop.run_in_executor(None, self._repair_callback, mode)
        recoverable = list(plan.get("recoverable_actions", []))
        blocking = list(plan.get("blocking_issues", []))
        warnings = list(plan.get("warnings", []))
        execution = dict(plan.get("execution", {}))

        lines = ["🧰 <b>Repair Confirm</b>" if mode in {"confirm", "apply"} else "🧰 <b>Repair Dry Run</b>"]
        lines.append(f"Status: <code>{plan.get('status', 'unknown')}</code>")
        lines.append(f"Safe mode: <code>{plan.get('safe_mode', False)}</code>")
        lines.append(f"Recoverable actions: {len(recoverable)}")
        lines.append(f"Blocking issues: {len(blocking)}")

        if execution:
            lines.append(
                f"Execution: <code>{execution.get('status', 'unknown')}</code> "
                f"(applied={execution.get('applied', 0)} failed={execution.get('failed', 0)})"
            )
            for error in execution.get("errors", [])[:5]:
                if error:
                    lines.append(f"  - error: {error}")

        if warnings:
            lines.append("\n<b>Warnings</b>")
            for warning in warnings[:5]:
                lines.append(f"  - {warning}")

        if recoverable:
            lines.append("\n<b>Recoverable</b>")
            for action in recoverable[:8]:
                lines.append(
                    f"  - {action.get('kind')} {action.get('symbol')} "
                    f"{action.get('side')} L{action.get('level_index')} "
                    f"qty={float(action.get('quantity', 0.0) or 0.0):.4f} "
                    f"@ {float(action.get('price', 0.0) or 0.0):.4f}"
                )

        if blocking:
            lines.append("\n<b>Blocking</b>")
            for issue in blocking[:8]:
                detail = issue.get("detail", "")
                level = issue.get("level_index", "-")
                lines.append(
                    f"  - {issue.get('kind')} {issue.get('symbol', '?')} "
                    f"L{level} {detail}".strip()
                )

        if len(recoverable) > 8 or len(blocking) > 8:
            lines.append("\n<i>只顯示前 8 筆，避免訊息過長</i>")
        return "\n".join(lines)

    async def _cmd_safe(self, args: list[str] | None = None) -> str:
        reason = "telegram_command"
        if self._safe_mode_callback is not None:
            self._safe_mode_callback(reason)
        return "🛡️ <b>Safe mode 已啟用</b>\nBot 會停止開新單，只保留監控與查詢。"
