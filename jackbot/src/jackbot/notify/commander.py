"""Jackbot Commander — interactive command handler for the running bot."""
from __future__ import annotations
import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
import httpx
import structlog

if TYPE_CHECKING:
    from jackbot.notify.telegram import TelegramBot
    from jackbot.strategy.day_trader import DayTrader
    from jackbot.portfolio.portfolio import Portfolio

logger = structlog.get_logger(__name__)

_POLL_INTERVAL = 3.0
_POLL_TIMEOUT = 2

class JackbotCommander:
    def __init__(
        self,
        bot: TelegramBot,
        trader: DayTrader,
        portfolio: Portfolio,
        stop_event: asyncio.Event,
    ) -> None:
        self._bot = bot
        self._trader = trader
        self._portfolio = portfolio
        self._stop_event = stop_event
        self._offset: int = 0
        self._authorized_chat_id = str(bot._chat_id)
        self._client = httpx.AsyncClient(timeout=10.0)

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
        params = {"offset": self._offset, "timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]}
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"): return
            updates = data.get("result", [])
        except httpx.ReadTimeout:
            return
            
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg: continue
            
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if self._authorized_chat_id and chat_id != self._authorized_chat_id:
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
            "pos": self._cmd_status,  # Alias
        }
        
        handler = handlers.get(cmd)
        if handler:
            reply = await handler()
            self._bot.send(reply)
        elif cmd:
            self._bot.send("❓ 未知指令。輸入 /help 查看可用指令。")

    async def _cmd_help(self) -> str:
        return (
            "🤖 <b>Jackbot 指令列表</b>\n\n"
            "/status   — 查看目前收益與網格狀態\n"
            "/balance  — 查看帳戶餘額與複利進度\n"
            "/help     — 顯示此幫助"
        )

    async def _cmd_status(self) -> str:
        status = self._trader.get_status()
        active = status.get("active_grids", [])
        
        lines = [f"📊 <b>Jackbot 狀態回報</b>"]
        lines.append(f"• 獲利: ${status.get('total_profit', 0):.2f} USDT")
        lines.append(f"• 今日目標: ${status.get('daily_target', 0)} USDT")
        lines.append(f"• 活躍網格: {len(active)} 個")
        
        for g in active:
            lines.append(f"\n🏷 <b>{g['symbol']} ({g['direction']})</b>")
            lines.append(f"  - 範圍: {g['range']}")
            lines.append(f"  - 槓桿: {g['leverage']}x")
            lines.append(f"  - 浮盈: ${g['unrealized_pnl']:.2f}")
            
            level_details = g.get("level_details", [])
            if level_details:
                lines.append("  - 網格價位:")
                for i, l in enumerate(level_details):
                    state = l["state"]
                    icon = "⏳" if "pending" in state else ("✅" if "filled" in state else "💰")
                    side = "買" if "buy" in state else ("賣" if "sell" in state else "")
                    lines.append(f"    {icon} {side} @ {l['price']:.2f}")
            
        return "\n".join(lines)

    async def _cmd_balance(self) -> str:
        equity = self._portfolio.available_capital
        profit = self._portfolio.total_pnl
        return (
            "💰 <b>帳戶餘額資訊</b>\n\n"
            f"• 當前權益: ${equity:.2f} USDT\n"
            f"• 累計盈虧: ${profit:.2f} USDT\n"
            f"• 策略模式: {self._trader._config.mode}"
        )
