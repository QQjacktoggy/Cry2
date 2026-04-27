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
    ) -> None:
        self._bot = bot
        self._trader = trader
        self._portfolio = portfolio
        self._client_api = client
        self._stop_event = stop_event
        self._offset: int = 0
        self._authorized_chat_id = str(bot._chat_id)
        self._client = httpx.AsyncClient(timeout=_POLL_TIMEOUT + 5.0)
        self._backoff_until = 0.0

    async def run(self) -> None:
        if not self._bot or not self._bot._enabled:
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
        if not data.get("ok"): return
        updates = data.get("result", [])
            
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
            "pos": self._cmd_status,
            "pnl": self._cmd_pnl,
            "wallet": self._cmd_wallet,
            "orders": self._cmd_orders,
        }
        
        handler = handlers.get(cmd)
        if handler:
            try:
                reply = await handler()
                self._bot.send(reply)
            except Exception as e:
                logger.error("commander_handler_error", cmd=cmd, error=str(e))
                self._bot.send(f"⚠️ 指令執行失敗: {e}")
        elif cmd:
            self._bot.send("❓ 未知指令。輸入 /help 查看可用指令。")

    async def _cmd_help(self) -> str:
        return (
            "🤖 <b>Jackbot 指令列表</b>\n\n"
            "/status   — 網格狀態與持倉\n"
            "/balance  — 帳戶權益與盈虧\n"
            "/pnl      — 詳細損益明細\n"
            "/wallet   — 真實錢包餘額 (Binance)\n"
            "/orders   — 當前交易所掛單\n"
            "/help     — 顯示此幫助"
        )

    async def _cmd_status(self) -> str:
        summary = self._portfolio.get_summary()
        status = self._trader.get_status()
        active = status.get("active_grids", [])

        lines = [f"📊 <b>Jackbot 網格狀態</b>"]
        lines.append(f"• 今日淨盈虧: ${summary['today_pnl']:.4f} USDT")
        lines.append(f"• 累計手續費: ${summary['total_commission']:.6f} USDT")
        lines.append(f"• 當前權益: <b>${summary['total_equity']:.2f}</b>")
        lines.append(f"• 活躍網格: {len(active)} 個")

        for g in active:
            lines.append(f"\n🏷 <b>{g['symbol']} ({g['direction']})</b>")
            lines.append(f"  - 範圍: {g['range']}")
            lines.append(f"  - 槓桿: {g['leverage']}x")
            lines.append(f"  - 浮盈: ${g['unrealized_pnl']:.2f}")
            
            level_details = g.get("level_details", [])
            if level_details:
                pending = [l for l in level_details if "pending" in l["state"]]
                filled = [l for l in level_details if "filled" in l["state"]]
                lines.append(f"  - 狀態: {len(filled)} 已成交, {len(pending)} 等待中")
            
        return "\n".join(lines)

    async def _cmd_pnl(self) -> str:
        summary = self._portfolio.get_summary()
        trades = self._portfolio._trades
        
        lines = ["📈 <b>損益詳細報告</b>\n"]
        lines.append(f"• 總獲利: ${summary['realized_pnl']:.4f} USDT")
        lines.append(f"• 交易次數: {summary['total_trades']} 次")
        
        # Breakdown by symbol
        if trades:
            lines.append("\n<b>幣種表現:</b>")
            sym_pnl = {}
            for t in trades:
                sym_pnl[t.symbol] = sym_pnl.get(t.symbol, 0.0) + t.profit_usd
            
            for sym, pnl in sorted(sym_pnl.items(), key=lambda x: -x[1]):
                emoji = "📈" if pnl >= 0 else "📉"
                lines.append(f"  {emoji} {sym}: ${pnl:+.4f}")
        
        return "\n".join(lines)

    async def _cmd_balance(self) -> str:
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

    async def _cmd_wallet(self) -> str:
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

    async def _cmd_orders(self) -> str:
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
