"""Jackbot Commander — interactive Telegram command handler.

Supported commands:
  /help    — list all commands
  /status  — full status: mode, PnL, active grids, positions, orders
  /price   — live price for all symbols
  /pos     — open positions (entry, liq, upnl, leverage)
  /orders  — open orders grouped by symbol
  /fills   — last 5 fills per symbol
  /pnl     — daily & cumulative PnL summary
  /balance — account USDT balance
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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


class JackbotCommander:
    def __init__(
        self,
        bot: "TelegramBot",
        trader: "DayTrader",
        portfolio: "Portfolio",
        stop_event: asyncio.Event,
        client: "BinanceClient | None" = None,
    ) -> None:
        self._bot = bot
        self._trader = trader
        self._portfolio = portfolio
        self._stop_event = stop_event
        self._client = client
        self._offset: int = 0
        self._authorized_chat_id = str(bot._chat_id)
        self._http = httpx.AsyncClient(timeout=10.0)

    # ── Main loop ────────────────────────────────────────────────────

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
        await self._http.aclose()

    async def _poll_once(self) -> None:
        url = f"https://api.telegram.org/bot{self._bot._token}/getUpdates"
        params = {
            "offset": self._offset,
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return
            updates = data.get("result", [])
        except httpx.ReadTimeout:
            return

        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if self._authorized_chat_id and chat_id != self._authorized_chat_id:
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                await self._dispatch(text)

    async def _dispatch(self, text: str) -> None:
        cmd = text.split()[0].lower().lstrip("/").split("@")[0]
        handlers = {
            "help":    self._cmd_help,
            "status":  self._cmd_status,
            "price":   self._cmd_price,
            "p":       self._cmd_price,
            "pos":     self._cmd_pos,
            "orders":  self._cmd_orders,
            "fills":   self._cmd_fills,
            "pnl":     self._cmd_pnl,
            "balance": self._cmd_balance,
            "grids":   self._cmd_grids,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                reply = await handler()
            except Exception as e:
                reply = f"⚠️ 指令執行失敗: {e}"
            self._bot.send(reply)
        elif cmd:
            self._bot.send("❓ 未知指令，輸入 /help 查看可用指令。")

    # ── Helpers ──────────────────────────────────────────────────────

    def _symbols(self) -> list[str]:
        return list(self._trader._cfg.symbols)

    def _fmt_price(self, sym: str, price: float) -> str:
        decimals = 1 if sym.startswith("BTC") else 2
        return f"{price:,.{decimals}f}"

    def _pnl_sign(self, v: float) -> str:
        return ("+" if v >= 0 else "") + f"{v:+.4f}"

    # ── Commands ─────────────────────────────────────────────────────

    async def _cmd_help(self) -> str:
        return (
            "🤖 <b>Jackbot V1 指令列表</b>\n\n"
            "/price   — 即時報價（所有交易對）\n"
            "/pos     — 開倉持倉明細\n"
            "/orders  — 掛單列表\n"
            "/fills   — 近期成交（每對最近 5 筆）\n"
            "/pnl     — 盈虧匯總\n"
            "/balance — 帳戶餘額\n"
            "/status  — 完整狀態快照\n"
            "/grids   — 網格引擎狀態\n"
            "/help    — 顯示此幫助"
        )

    async def _cmd_price(self) -> str:
        if not self._client:
            return "⚠️ 無法取得即時報價（client 未連接）"
        lines = ["💹 <b>即時報價</b>"]
        for sym in self._symbols():
            try:
                price = self._client.get_ticker_price(sym)
                lat = self._client.ping()
                lines.append(f"• <b>{sym}</b>: ${self._fmt_price(sym, price)}  <i>({lat:.0f}ms)</i>")
            except Exception as e:
                lines.append(f"• {sym}: ⚠️ {e}")
        lines.append(f"\n🕐 {datetime.now(UTC).strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)

    async def _cmd_pos(self) -> str:
        if not self._client:
            return "⚠️ 無法查詢持倉（client 未連接）"
        lines = ["📐 <b>目前持倉</b>"]
        any_pos = False
        for sym in self._symbols():
            try:
                raw = self._client.get_position(sym)
                amt = float(raw.get("positionAmt", 0))
                if abs(amt) < 1e-9:
                    continue
                any_pos = True
                side = "LONG 🟢" if amt > 0 else "SHORT 🔴"
                entry = float(raw.get("entryPrice", 0))
                liq = float(raw.get("liquidationPrice", 0))
                upnl = float(raw.get("unrealizedProfit", 0))
                lev = int(float(raw.get("leverage", 1)))
                dec = 1 if sym.startswith("BTC") else 2
                pnl_icon = "📈" if upnl >= 0 else "📉"
                lines.append(
                    f"\n<b>{sym}</b> {side} {lev}x\n"
                    f"  數量: {abs(amt):.4f}\n"
                    f"  進場: ${entry:,.{dec}f}\n"
                    f"  強平: ${liq:,.{dec}f}\n"
                    f"  浮盈: {pnl_icon} ${upnl:+.4f} USDT"
                )
            except Exception as e:
                lines.append(f"\n{sym}: ⚠️ {e}")
        if not any_pos:
            lines.append("\n（無持倉）")
        return "\n".join(lines)

    async def _cmd_orders(self) -> str:
        if not self._client:
            return "⚠️ 無法查詢掛單（client 未連接）"
        lines = ["📋 <b>掛單明細</b>"]
        total = 0
        for sym in self._symbols():
            try:
                orders = self._client.get_open_orders(sym)
                if not orders:
                    continue
                total += len(orders)
                dec = 1 if sym.startswith("BTC") else 2
                buys = sorted([o for o in orders if o.get("side") == "BUY"],
                               key=lambda x: float(x.get("price", 0)), reverse=True)
                sells = sorted([o for o in orders if o.get("side") == "SELL"],
                                key=lambda x: float(x.get("price", 0)), reverse=True)
                lines.append(f"\n<b>{sym}</b>（{len(orders)} 筆）")
                for o in sells:
                    lines.append(f"  🔴 賣 {float(o.get('origQty',0)):.4f} @ ${float(o.get('price',0)):,.{dec}f}")
                # current price separator
                try:
                    px = self._client.get_ticker_price(sym)
                    lines.append(f"  ─── 現價 ${self._fmt_price(sym, px)} ───")
                except Exception:
                    pass
                for o in buys:
                    lines.append(f"  🟢 買 {float(o.get('origQty',0)):.4f} @ ${float(o.get('price',0)):,.{dec}f}")
            except Exception as e:
                lines.append(f"\n{sym}: ⚠️ {e}")
        if total == 0:
            lines.append("\n（無掛單）")
        else:
            lines.append(f"\n共 {total} 筆掛單")
        return "\n".join(lines)

    async def _cmd_fills(self) -> str:
        if not self._client:
            return "⚠️ 無法查詢成交（client 未連接）"
        lines = ["⚡ <b>近期成交</b>（每對最近 5 筆）"]
        any_fill = False
        for sym in self._symbols():
            try:
                fills = self._client._signed_get(
                    "/fapi/v1/userTrades", {"symbol": sym, "limit": 5}
                )
                if not fills:
                    continue
                any_fill = True
                dec = 1 if sym.startswith("BTC") else 2
                lines.append(f"\n<b>{sym}</b>")
                for f in reversed(fills):
                    ts = datetime.fromtimestamp(f["time"] / 1000, tz=UTC).strftime("%H:%M:%S")
                    side = "買🟢" if f.get("side") == "BUY" else "賣🔴"
                    px = float(f.get("price", 0))
                    qty = float(f.get("qty", 0))
                    pnl = float(f.get("realizedPnl", 0))
                    mk = "M" if f.get("maker") else "T"
                    pnl_str = f"${pnl:+.4f}" if pnl != 0 else "—"
                    lines.append(f"  {ts} {side} {qty:.4f}@${px:,.{dec}f} PnL:{pnl_str} [{mk}]")
            except Exception as e:
                lines.append(f"\n{sym}: ⚠️ {e}")
        if not any_fill:
            lines.append("\n（無成交記錄）")
        return "\n".join(lines)

    async def _cmd_pnl(self) -> str:
        status = self._trader.get_status()
        daily = status.get("daily_profit", 0.0)
        target = status.get("daily_target", 10.0)
        total = status.get("total_profit", 0.0)
        loss = status.get("daily_loss", 0.0)
        halted = status.get("halted", False)

        pct = daily / target * 100 if target > 0 else 0
        bar_n = min(int(pct / 5), 20)
        bar = "█" * bar_n + "░" * (20 - bar_n)

        lines = [
            "📊 <b>盈虧匯總</b>\n",
            f"今日利潤: ${daily:+.4f} USDT",
            f"今日目標: ${target:.2f} USDT",
            f"進度: [{bar}] {pct:.1f}%",
            f"今日虧損: ${loss:.4f} USDT",
            f"累計盈虧: ${total:+.4f} USDT",
        ]
        if halted:
            lines.append("\n🚨 <b>交易已暫停</b>")

        # Add live upnl from exchange if available
        if self._client:
            upnl_total = 0.0
            for sym in self._symbols():
                try:
                    raw = self._client.get_position(sym)
                    upnl_total += float(raw.get("unrealizedProfit", 0))
                except Exception:
                    pass
            lines.append(f"浮動盈虧: ${upnl_total:+.4f} USDT")

        lines.append(f"\n🕐 {datetime.now(UTC).strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)

    async def _cmd_balance(self) -> str:
        equity = self._portfolio.available_capital
        profit = self._portfolio.total_pnl
        mode = self._trader._mode.value if hasattr(self._trader, "_mode") else "—"

        lines = [
            "💰 <b>帳戶資訊</b>\n",
            f"Bot 帳本餘額: ${equity:.2f} USDT",
            f"Bot 累計盈虧: ${profit:+.4f} USDT",
            f"策略模式: {mode}",
        ]

        if self._client:
            try:
                live_bal = self._client.get_balance()
                lat = self._client.ping()
                lines.append(f"交易所可用: ${live_bal:.4f} USDT")
                lines.append(f"延遲: {lat:.0f} ms")
            except Exception as e:
                lines.append(f"⚠️ 交易所查詢失敗: {e}")

        return "\n".join(lines)

    async def _cmd_status(self) -> str:
        """Full snapshot: PnL + positions + orders + grid state."""
        parts = []

        # 1. PnL summary
        parts.append(await self._cmd_pnl())

        # 2. Positions
        parts.append(await self._cmd_pos())

        # 3. Orders (condensed)
        parts.append(await self._cmd_orders())

        return "\n\n".join(parts)

    async def _cmd_grids(self) -> str:
        status = self._trader.get_status()
        active = status.get("active_grids", [])
        bar_counts = status.get("bar_counts", {})

        lines = ["🏗 <b>網格引擎狀態</b>"]

        if bar_counts:
            warmup = 50
            prog = ", ".join(f"{s}: {c}/{warmup}" for s, c in bar_counts.items())
            lines.append(f"預熱: {prog}")

        if not active:
            lines.append("（無活躍網格）")
        else:
            for g in active:
                lines.append(
                    f"\n<b>{g['symbol']}</b> {g['direction']} "
                    f"{g['levels']} 格 {g['leverage']}x\n"
                    f"  範圍: {g['range']}\n"
                    f"  已成對: {g['matched']} 次\n"
                    f"  實現利潤: ${g['realized_profit']:+.4f}\n"
                    f"  浮動盈虧: ${g['unrealized_pnl']:+.4f}\n"
                    f"  運行: {g['age_minutes']:.0f} 分鐘"
                )
                lvls = g.get("level_details", [])
                if lvls:
                    lines.append("  網格價位:")
                    for lv in lvls:
                        st = lv["state"]
                        icon = "🟢" if "buy" in st else ("🔴" if "sell" in st else "⬜")
                        filled_icon = "✅" if "filled" in st else "⏳"
                        lines.append(f"    {filled_icon}{icon} @ {lv['price']:.2f}")

        return "\n".join(lines)
