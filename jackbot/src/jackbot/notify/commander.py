"""Jackbot Commander — interactive command handler for the running bot."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Callable

import httpx
import structlog

if TYPE_CHECKING:
    from jackbot.exchange.client import BinanceClient
    from jackbot.notify.telegram import TelegramBot
    from jackbot.portfolio.portfolio import Portfolio
    from jackbot.strategy.day_trader import DayTrader

logger = structlog.get_logger(__name__)

_POLL_INTERVAL = 3.0
_POLL_TIMEOUT = 2
_CONFIRM_TTL_SEC = 90


class JackbotCommander:
    def __init__(
        self,
        bot: TelegramBot,
        trader: DayTrader,
        portfolio: Portfolio,
        stop_event: asyncio.Event,
        client: BinanceClient | None = None,
        get_runner_snapshot: Callable[[], dict] | None = None,
        close_all_now: Callable[[str], int] | None = None,
        halt_trading: Callable[[str], None] | None = None,
        resume_trading: Callable[[], bool] | None = None,
        set_mode: Callable[[str], bool] | None = None,
        stop_runner: Callable[[str], None] | None = None,
    ) -> None:
        self._bot = bot
        self._trader = trader
        self._portfolio = portfolio
        self._stop_event = stop_event
        self._client_ref = client
        self._get_runner_snapshot = get_runner_snapshot
        self._close_all_now = close_all_now
        self._halt_trading = halt_trading
        self._resume_trading = resume_trading
        self._set_mode = set_mode
        self._stop_runner = stop_runner

        self._offset: int = 0
        self._authorized_chat_id = str(bot._chat_id)
        self._client = httpx.AsyncClient(timeout=10.0)
        self._pending_danger: dict[str, dict[str, Any]] = {}

    async def run(self) -> None:
        if not self._bot or not self._bot._enabled:
            return
        logger.info("commander_started", chat_id=self._authorized_chat_id)
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception as exc:
                logger.warning("commander_poll_error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)
        logger.info("commander_stopped")
        await self._client.aclose()

    async def _poll_once(self) -> None:
        url = f"https://api.telegram.org/bot{self._bot._token}/getUpdates"
        params = {
            "offset": self._offset,
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return
            updates = data.get("result", [])
            if updates:
                logger.info("commander_updates_received", count=len(updates))
        except httpx.ReadTimeout:
            return

        for update in updates:
            self._offset = update["update_id"] + 1

            # Inline keyboard callbacks
            cb = update.get("callback_query")
            if cb:
                logger.info(
                    "commander_callback_received",
                    callback_id=cb.get("id", ""),
                    data=cb.get("data", ""),
                    from_chat=str(cb.get("message", {}).get("chat", {}).get("id", "")),
                )
                await self._handle_callback_query(cb)
                continue

            msg = update.get("message")
            if not msg:
                continue

            chat_id = str(msg.get("chat", {}).get("id", ""))
            if self._authorized_chat_id and chat_id != self._authorized_chat_id:
                continue

            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                logger.info("commander_command_received", command=text, chat_id=chat_id)
                await self._dispatch(text, chat_id)

    async def _handle_callback_query(self, cb: dict[str, Any]) -> None:
        cb_id = cb.get("id", "")
        data = (cb.get("data") or "").strip()
        msg = cb.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if self._authorized_chat_id and chat_id != self._authorized_chat_id:
            await self._answer_callback(cb_id, "Unauthorized", show_alert=True)
            return

        if data.startswith("page:"):
            page = data.removeprefix("page:").strip().lower() or "monitor"
            self._bot.send_menu(page=page)
            await self._answer_callback(cb_id, f"切換到 {page}", show_alert=False)
            return

        if data.startswith("danger:"):
            action = data.removeprefix("danger:").strip().lower()
            if action not in {"closeall", "shutdown"}:
                await self._answer_callback(cb_id, "未知危險操作", show_alert=False)
                return

            self._pending_danger[chat_id] = {
                "action": action,
                "expires_at": datetime.now(UTC).timestamp() + _CONFIRM_TTL_SEC,
            }
            self._bot.send(
                (
                    f"⚠️ <b>危險操作確認</b>\n"
                    f"你即將執行 <b>{action}</b>。\n"
                    f"請在 {_CONFIRM_TTL_SEC} 秒內點擊 Confirm。"
                ),
                reply_markup=self._bot.build_confirm_keyboard(action),
            )
            await self._answer_callback(cb_id, f"請確認 {action}", show_alert=False)
            return

        if data.startswith("confirm:"):
            action = data.removeprefix("confirm:").strip().lower()
            pending = self._pending_danger.get(chat_id)
            now_ts = datetime.now(UTC).timestamp()
            if (
                not pending
                or pending.get("action") != action
                or float(pending.get("expires_at", 0)) < now_ts
            ):
                await self._answer_callback(cb_id, "確認已過期，請重新操作", show_alert=True)
                return

            self._pending_danger.pop(chat_id, None)
            await self._answer_callback(cb_id, f"已確認 {action}", show_alert=False)
            await self._dispatch(f"/{action} confirm", chat_id)
            return

        if data.startswith("cancel:"):
            self._pending_danger.pop(chat_id, None)
            await self._answer_callback(cb_id, "已取消操作", show_alert=False)
            self._bot.send_menu(page="system")
            return

        if not data.startswith("cmd:"):
            await self._answer_callback(cb_id, "Unknown action", show_alert=False)
            return

        cmd_text = "/" + data.removeprefix("cmd:")
        await self._answer_callback(cb_id, f"執行 {cmd_text}", show_alert=False)
        await self._dispatch(cmd_text, chat_id)

    async def _answer_callback(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> None:
        if not callback_query_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self._bot._token}/answerCallbackQuery"
            resp = await self._client.post(
                url,
                json={
                    "callback_query_id": callback_query_id,
                    "text": text[:180],
                    "show_alert": bool(show_alert),
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "answer_callback_bad_status",
                    status_code=resp.status_code,
                    body=resp.text[:300],
                )
        except Exception as exc:
            logger.warning("answer_callback_failed", error=str(exc))

    def _snapshot(self) -> dict:
        if self._get_runner_snapshot is not None:
            return self._get_runner_snapshot()
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "trader": self._trader.get_status(),
            "portfolio": self._portfolio.get_summary(),
            "portfolio_symbol_pnl": self._portfolio.get_symbol_pnl(),
            "portfolio_recent_trades": self._portfolio.get_recent_trades(limit=10),
            "exchange": {"error": "runner snapshot unavailable"},
            "runtime": {},
            "recent_events": [],
            "gcp": {"available": False, "error": "runner snapshot unavailable"},
            "symbols": [],
            "timeframe": "",
        }

    async def _dispatch(self, text: str, chat_id: str = "") -> None:
        parts = text.split()
        cmd = parts[0].lower().lstrip("/")
        args = parts[1:]

        handlers = {
            "start": self._cmd_help,
            "help": self._cmd_help,
            "menu": self._cmd_help,
            "status": self._cmd_status,
            "balance": self._cmd_balance,
            "pos": self._cmd_positions,
            "positions": self._cmd_positions,
            "orders": self._cmd_orders,
            "runtime": self._cmd_runtime,
            "report": self._cmd_report,
            "summary": self._cmd_report,
            "recent": self._cmd_recent,
            "gcp": self._cmd_gcp,
            "halt": self._cmd_halt,
            "resume": self._cmd_resume,
            "mode": self._cmd_mode,
            "closeall": self._cmd_close_all,
            "shutdown": self._cmd_shutdown,
            "ping": self._cmd_ping,
        }

        handler = handlers.get(cmd)
        if not handler:
            self._bot.send("❓ 未知指令。輸入 /help 查看可用指令。")
            return

        try:
            reply = await handler(args, chat_id)
            if reply:
                self._bot.send_long(reply)
        except Exception as exc:
            logger.warning("commander_dispatch_error", cmd=cmd, error=str(exc))
            self._bot.send(f"❌ 指令執行失敗：<code>{str(exc)[:700]}</code>")

    async def _cmd_help(self, _args: list[str], _chat_id: str) -> str:
        self._bot.send_menu(page="monitor")
        return (
            "🤖 <b>Jackbot 指令中心</b>\n\n"
            "<b>監控查詢</b>\n"
            "/status — 交易核心狀態\n"
            "/balance — 資金與累計績效\n"
            "/positions — 交易所倉位快照\n"
            "/orders — 交易所掛單快照\n"
            "/runtime — 程式執行統計與錯誤\n"
            "/recent — 最近事件與成交\n"
            "/gcp — GCP/VM 資源狀態\n"
            "/report — 詳細總結報告\n\n"
            "<b>操作命令</b>\n"
            "/halt — 暫停交易（保留流程）\n"
            "/resume — 恢復交易\n"
            "/mode aggressive|conservative — 強制模式\n"
            "/closeall — 關閉所有網格並撤單\n"
            "/shutdown — 安全停止程序\n"
            "/ping — 健康檢查\n\n"
            "⚠️ 危險操作（/closeall, /shutdown）需要二次確認"
        )

    async def _cmd_status(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        trader = snap.get("trader", {})
        portfolio = snap.get("portfolio", {})
        active = trader.get("active_grids", [])
        lines = ["📊 <b>交易狀態</b>"]
        lines.append(f"模式: {trader.get('mode', '?')} | halted: {trader.get('halted', False)}")
        lines.append(f"日利潤: ${trader.get('daily_profit', 0):.4f} / ${trader.get('daily_target', 0):.2f}")
        lines.append(f"日虧損: ${trader.get('daily_loss', 0):.4f} | resets: {trader.get('daily_resets', 0)}")
        lines.append(f"總已實現: ${portfolio.get('total_pnl', 0):.4f}")
        lines.append(f"活躍網格: {len(active)}")

        for g in active[:8]:
            lines.append(
                f"- {g.get('symbol')} {g.get('direction')} lev:{g.get('leverage')} "
                f"pending:{g.get('pending_orders')} matched:{g.get('matched')} "
                f"net:${g.get('net_pnl', 0):.4f}"
            )

        return "\n".join(lines)

    async def _cmd_balance(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        portfolio = snap.get("portfolio", {})
        symbol_pnl = snap.get("portfolio_symbol_pnl", {})
        lines = ["💰 <b>資金與績效</b>"]
        lines.append(f"初始資金: ${portfolio.get('initial_capital', 0):.2f}")
        lines.append(f"可用資金: ${portfolio.get('available_capital', 0):.2f}")
        lines.append(f"總盈虧: ${portfolio.get('total_pnl', 0):.4f}")
        lines.append(f"今日盈虧: ${portfolio.get('today_pnl', 0):.4f}")
        lines.append(f"成交筆數: {portfolio.get('total_trades', 0)}")
        if symbol_pnl:
            lines.append("分幣種盈虧:")
            for sym, pnl in symbol_pnl.items():
                lines.append(f"- {sym}: ${pnl:.4f}")
        return "\n".join(lines)

    async def _cmd_positions(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        exchange = snap.get("exchange", {})
        positions = exchange.get("positions", {})
        lines = ["📌 <b>交易所倉位</b>"]
        if exchange.get("error"):
            lines.append(f"錯誤: <code>{exchange['error'][:500]}</code>")
            return "\n".join(lines)
        if not positions:
            lines.append("目前無倉位資料")
            return "\n".join(lines)
        for sym, p in positions.items():
            lines.append(
                f"- {sym}: qty={p.get('qty', 0)} entry={p.get('entry', 0)} upnl={p.get('upnl', 0)}"
            )
        return "\n".join(lines)

    async def _cmd_orders(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        exchange = snap.get("exchange", {})
        open_orders = exchange.get("open_orders", {})
        lines = ["🧾 <b>交易所掛單</b>"]
        if exchange.get("error"):
            lines.append(f"錯誤: <code>{exchange['error'][:500]}</code>")
            return "\n".join(lines)
        total = sum(int(v) for v in open_orders.values()) if open_orders else 0
        lines.append(f"總掛單數: {total}")
        for sym, cnt in open_orders.items():
            lines.append(f"- {sym}: {cnt}")
        return "\n".join(lines)

    async def _cmd_runtime(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        runtime = snap.get("runtime", {})
        lines = ["⚙️ <b>執行統計</b>"]
        lines.append(f"signals_total: {runtime.get('signals_total', 0)}")
        lines.append(f"orders_placed: {runtime.get('orders_placed', 0)}")
        lines.append(f"orders_failed: {runtime.get('orders_failed', 0)}")
        lines.append(f"cancel_requests: {runtime.get('cancel_requests', 0)}")
        if runtime.get("last_error"):
            lines.append(f"last_error: <code>{str(runtime['last_error'])[:500]}</code>")
        return "\n".join(lines)

    async def _cmd_recent(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        events = snap.get("recent_events", [])
        trades = snap.get("portfolio_recent_trades", [])
        lines = ["🕒 <b>最近事件</b>"]
        for e in events[-8:]:
            ts = str(e.get("ts", ""))[11:19]
            lines.append(f"- {ts} {e.get('kind')} {e.get('data', {})}")

        lines.append("\n💹 <b>最近成交摘要</b>")
        if not trades:
            lines.append("- 暫無")
        for t in trades[:8]:
            ts = str(t.get("timestamp", ""))[11:19]
            lines.append(
                f"- {ts} {t.get('symbol')} qty={t.get('quantity')} pnl=${float(t.get('profit_usd', 0)):.4f}"
            )
        return "\n".join(lines)

    async def _cmd_gcp(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        gcp = snap.get("gcp", {})
        lines = ["☁️ <b>GCP / VM 狀態</b>"]
        lines.append(f"metadata_available: {gcp.get('available', False)}")
        lines.append(f"project: {gcp.get('project_id', '?')}")
        lines.append(f"instance: {gcp.get('instance_name', '?')} ({gcp.get('instance_id', '?')})")
        lines.append(f"zone: {gcp.get('zone', '?')} | machine: {gcp.get('machine_type', '?')}")
        lines.append(f"host: {gcp.get('hostname', '?')}")
        lines.append(f"os: {gcp.get('os', '?')}")
        lines.append(f"cpu_load_1m: {gcp.get('cpu_load_1m', 0)}")
        mem = gcp.get("memory", {})
        disk = gcp.get("disk", {})
        if mem:
            lines.append(
                f"memory: {mem.get('used_mb', 0)} / {mem.get('total_mb', 0)} MB ({mem.get('used_pct', 0)}%)"
            )
        if disk:
            lines.append(
                f"disk: {disk.get('used_gb', 0)} / {disk.get('total_gb', 0)} GB ({disk.get('used_pct', 0)}%)"
            )
        if gcp.get("error"):
            lines.append(f"error: <code>{str(gcp['error'])[:500]}</code>")
        return "\n".join(lines)

    async def _cmd_report(self, _args: list[str], _chat_id: str) -> str:
        snap = self._snapshot()
        trader = snap.get("trader", {})
        portfolio = snap.get("portfolio", {})
        exchange = snap.get("exchange", {})
        runtime = snap.get("runtime", {})
        gcp = snap.get("gcp", {})

        lines = ["🧠 <b>Jackbot 詳細總結報告</b>"]
        lines.append(f"時間: {snap.get('timestamp', '?')}")
        lines.append(f"Symbols: {', '.join(snap.get('symbols', []))} | TF: {snap.get('timeframe', '?')}")
        lines.append("")
        lines.append("<b>1) 交易核心</b>")
        lines.append(f"mode={trader.get('mode')} halted={trader.get('halted')} resets={trader.get('daily_resets')}")
        lines.append(
            f"daily_profit=${trader.get('daily_profit', 0):.4f} / ${trader.get('daily_target', 0):.2f} "
            f"daily_loss=${trader.get('daily_loss', 0):.4f}"
        )
        lines.append(f"active_grids={len(trader.get('active_grids', []))} total_profit=${trader.get('total_profit', 0):.4f}")
        lines.append("")
        lines.append("<b>2) 資金績效</b>")
        lines.append(
            f"initial=${portfolio.get('initial_capital', 0):.2f} available=${portfolio.get('available_capital', 0):.2f} "
            f"total_pnl=${portfolio.get('total_pnl', 0):.4f} today=${portfolio.get('today_pnl', 0):.4f}"
        )
        lines.append(f"total_trades={portfolio.get('total_trades', 0)}")
        sym_pnl = snap.get("portfolio_symbol_pnl", {})
        if sym_pnl:
            lines.append("symbol_pnl=" + ", ".join(f"{k}:${v:.4f}" for k, v in sym_pnl.items()))
        lines.append("")
        lines.append("<b>3) 交易所快照</b>")
        lines.append(f"balance={exchange.get('balance')} error={exchange.get('error', '')}")
        lines.append("open_orders=" + ", ".join(f"{k}:{v}" for k, v in exchange.get("open_orders", {}).items()))
        pos = exchange.get("positions", {})
        for sym, p in pos.items():
            lines.append(f"{sym} qty={p.get('qty')} entry={p.get('entry')} upnl={p.get('upnl')}")
        lines.append("")
        lines.append("<b>4) Runtime</b>")
        lines.append(
            f"signals={runtime.get('signals_total', 0)} placed={runtime.get('orders_placed', 0)} "
            f"failed={runtime.get('orders_failed', 0)} cancels={runtime.get('cancel_requests', 0)}"
        )
        if runtime.get("last_error"):
            lines.append(f"last_error=<code>{str(runtime['last_error'])[:350]}</code>")
        lines.append("")
        lines.append("<b>5) GCP</b>")
        lines.append(
            f"project={gcp.get('project_id', '?')} instance={gcp.get('instance_name', '?')} "
            f"zone={gcp.get('zone', '?')} machine={gcp.get('machine_type', '?')}"
        )
        mem = gcp.get("memory", {})
        disk = gcp.get("disk", {})
        if mem and disk:
            lines.append(
                f"mem={mem.get('used_mb', 0)}/{mem.get('total_mb', 0)}MB ({mem.get('used_pct', 0)}%) "
                f"disk={disk.get('used_gb', 0)}/{disk.get('total_gb', 0)}GB ({disk.get('used_pct', 0)}%)"
            )
        if gcp.get("error"):
            lines.append(f"gcp_error=<code>{str(gcp['error'])[:300]}</code>")

        return "\n".join(lines)

    async def _cmd_halt(self, _args: list[str], _chat_id: str) -> str:
        if self._halt_trading is None:
            return "⚠️ halt 功能不可用"
        self._halt_trading("telegram_halt")
        return "🛑 已執行 halt，交易流程暫停。"

    async def _cmd_resume(self, _args: list[str], _chat_id: str) -> str:
        if self._resume_trading is None:
            return "⚠️ resume 功能不可用"
        ok = self._resume_trading()
        return "✅ 已恢復交易。" if ok else "⚠️ 無法恢復：目前風險條件不允許。"

    async def _cmd_mode(self, args: list[str], _chat_id: str) -> str:
        if self._set_mode is None:
            return "⚠️ mode 功能不可用"
        if not args:
            return "用法: /mode aggressive 或 /mode conservative"
        target = args[0].strip().lower()
        ok = self._set_mode(target)
        return f"✅ 模式已切換為 {target}" if ok else "❌ 模式值無效，只能 aggressive/conservative"

    async def _cmd_close_all(self, args: list[str], _chat_id: str) -> str:
        if self._close_all_now is None:
            return "⚠️ closeall 功能不可用"
        if not args or args[0].strip().lower() != "confirm":
            return (
                "⚠️ <b>危險操作保護</b>\n"
                "請再次確認：/closeall confirm\n"
                "或使用按鈕二次確認。"
            )
        count = self._close_all_now("telegram_close_all")
        return f"🧹 已觸發 closeall，送出 {count} 筆關閉/撤單信號。"

    async def _cmd_shutdown(self, args: list[str], _chat_id: str) -> str:
        if self._stop_runner is None:
            return "⚠️ shutdown 功能不可用"
        if not args or args[0].strip().lower() != "confirm":
            return (
                "⚠️ <b>危險操作保護</b>\n"
                "請再次確認：/shutdown confirm\n"
                "或使用按鈕二次確認。"
            )
        self._stop_runner("telegram_shutdown")
        return "🛑 已請求安全關機，程序將停止。"

    async def _cmd_ping(self, _args: list[str], _chat_id: str) -> str:
        now = datetime.now(UTC).isoformat()
        return f"🏓 pong\nserver_time={now}"
