"""Telegram notifications for Jackbot_V1."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class TelegramBot:
    """Telegram notification sender.

    Sends formatted messages for grid events. Bot commands are
    handled externally (future: add interactive command handling).
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = True,
    ) -> None:
        self._token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = enabled and bool(self._token) and bool(self._chat_id)

        if self._enabled:
            try:
                import httpx
                self._client = httpx.Client(timeout=10.0)
            except ImportError:
                self._enabled = False
                logger.warning("httpx not available, telegram disabled")

    def send(self, text: str) -> None:
        """Send a message to the configured chat."""
        if not self._enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            self._client.post(url, json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
        except Exception as e:
            logger.warning("telegram_send_failed", error=str(e))

    # ── Formatted messages ────────────────────────────────────────────

    def notify_grid_created(self, grid_info: dict) -> None:
        msg = (
            f"🟢 <b>新網格建立</b>\n"
            f"幣種: {grid_info.get('symbol', '?')}\n"
            f"方向: {grid_info.get('direction', '?')}\n"
            f"格數: {grid_info.get('levels', '?')}\n"
            f"槓桿: {grid_info.get('leverage', '?')}x\n"
            f"區間: {grid_info.get('range', '?')}"
        )
        self.send(msg)

    def notify_grid_profit(self, profit: float, total: float, target: float, equity: float = 0, fee: float = 0) -> None:
        self.notify_grid_cycle(
            symbol="",
            grid_id="",
            level_index=-1,
            buy_price=0.0,
            sell_price=0.0,
            quantity=0.0,
            gross_profit=profit,
            commission=fee,
            session_net=total,
            target=target,
            equity=equity,
        )

    def notify_grid_cycle(
        self,
        *,
        symbol: str,
        grid_id: str,
        level_index: int,
        buy_price: float,
        sell_price: float,
        quantity: float,
        gross_profit: float,
        commission: float,
        session_net: float,
        target: float,
        equity: float = 0,
    ) -> None:
        """Send the single authoritative notification for a completed grid cycle."""
        net_profit = gross_profit - commission
        pct = session_net / target * 100 if target > 0 else 0
        bar_len = min(20, max(0, int(pct / 5)))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        context = f"{symbol} L{level_index}" if symbol else "Grid cycle"
        prices = ""
        if buy_price > 0 and sell_price > 0 and quantity > 0:
            prices = (
                f"\nBuy: <code>{buy_price:.4f}</code> qty <code>{quantity:.4f}</code>"
                f"\nSell: <code>{sell_price:.4f}</code>"
            )
        equity_str = f"\n權益(含未實現): <b>${equity:.2f}</b>" if equity > 0 else ""
        msg = (
            f"✅ <b>Grid 成交完成</b>\n"
            f"{context}{prices}\n"
            f"Gross: <code>${gross_profit:+.4f}</code>\n"
            f"Fee: <code>-${commission:.6f}</code>\n"
            f"Net: <b>${net_profit:+.4f}</b>\n"
            f"啟動後淨盈餘: <b>${session_net:+.4f}</b> / ${target:.2f}\n"
            f"進度: [{bar}] {pct:.1f}%"
            f"{equity_str}\n"
            f"Grid: <code>{grid_id or '-'}</code>"
        )
        self.send(msg)

    def _notify_grid_profit_legacy(self, profit: float, total: float, target: float, equity: float = 0, fee: float = 0) -> None:
        pct = total / target * 100 if target > 0 else 0
        bar_len = min(20, int(pct / 5))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        equity_str = f"\n權益(含未實現): <b>${equity:.2f}</b>" if equity > 0 else ""
        fee_str = f"\n手續費: <code>-${fee:.6f}</code>" if fee > 0 else ""
        msg = (
            f"💰 <b>格間利潤</b> +${profit:.4f}{fee_str}\n"
            f"啟動後目標進度: [{bar}] {pct:.1f}%\n"
            f"啟動後淨獲利: ${total:.4f} / ${target:.2f}"
            f"{equity_str}"
        )
        self.send(msg)

    def notify_entry_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float,
        commission_asset: str = "",
        grid_id: str = "",
        level_index: int = -1,
        daily_profit: float = 0.0,
        daily_target: float = 0.0,
    ) -> None:
        """Entry fill confirmation (pnl=0 fills). Shows cost only — profit comes later via notify_grid_cycle."""
        progress = ""
        if daily_target > 0:
            pct = max(min(daily_profit / daily_target * 100, 100.0), 0.0)
            progress = f"\n啟動後淨盈餘: ${daily_profit:+.4f}  ({pct:.1f}% / ${daily_target:.2f})"
        elif daily_profit != 0.0:
            progress = f"\n啟動後淨盈餘: ${daily_profit:+.4f}"

        grid_context = ""
        if grid_id:
            grid_context = f"\nGrid: <code>{grid_id}</code> L{level_index if level_index >= 0 else '-'}"

        msg = (
            f"📥 <b>Grid 開倉</b>\n"
            f"{symbol} {side} <code>{quantity:.4f}</code> @ <code>{price:.4f}</code>\n"
            f"手續費: <code>-${commission:.6f} {commission_asset or 'USDC'}</code>"
            f"{grid_context}"
            f"{progress}"
        )
        self.send(msg)

    def notify_unmatched_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        exchange_pnl: float,
        commission: float,
        commission_asset: str = "",
        grid_id: str = "",
    ) -> None:
        """Exit fill that closed a position but had no matching entry in the grid tracker.

        This happens when a grid breaks out or is closed before the counter-order is paired.
        Shows the exchange-reported PnL for transparency — this is Binance's position accounting,
        not grid profit, so it may differ from what the grid would have calculated.
        """
        pnl_prefix = "+" if exchange_pnl >= 0 else ""
        grid_str = f"\nGrid: <code>{grid_id}</code> (已關閉)" if grid_id else ""
        msg = (
            f"📤 <b>成交 (無配對)</b>\n"
            f"{symbol} {side} <code>{quantity:.4f}</code> @ <code>{price:.4f}</code>\n"
            f"交易所 PnL: <code>{pnl_prefix}${exchange_pnl:.5f}</code>\n"
            f"手續費: <code>-${commission:.6f} {commission_asset or 'USDC'}</code>"
            f"{grid_str}"
        )
        self.send(msg)

    def notify_exchange_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float,
        realized_pnl: float,
        order_id: str,
        client_order_id: str,
        commission_asset: str = "",
        trade_id: str = "",
        grid_id: str = "",
        level_index: int = -1,
        daily_profit: float = 0.0,
        daily_target: float = 0.0,
    ) -> None:
        """Legacy method — routes to the appropriate focused notifier."""
        if side == "BUY":
            self.notify_entry_fill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                commission=commission,
                commission_asset=commission_asset,
                grid_id=grid_id,
                level_index=level_index,
                daily_profit=daily_profit,
                daily_target=daily_target,
            )
        else:
            self.notify_unmatched_fill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                exchange_pnl=realized_pnl,
                commission=commission,
                commission_asset=commission_asset,
                grid_id=grid_id,
            )

    def notify_reconcile(self, report: dict, safe_mode: bool = False) -> None:
        status = report.get("status", "?")
        warnings = report.get("warnings", [])
        msg = (
            f"⚠️ <b>Exchange Reconcile</b>\n"
            f"狀態: <code>{status}</code>\n"
            f"Safe mode: <code>{'ON' if safe_mode else 'OFF'}</code>\n"
            f"Positions: {len(report.get('positions', []))}\n"
            f"Open orders: {len(report.get('open_orders', []))}\n"
            f"Orphan orders: {len(report.get('orphan_orders', []))}\n"
            f"Orphan positions: {len(report.get('orphan_positions', []))}"
        )
        if warnings:
            msg += "\n" + "\n".join(f"  - {w}" for w in warnings[:5])
        self.send(msg)

    def notify_alert(self, title: str, message: str) -> None:
        """Send a high-priority system alert."""
        if not self._enabled:
            return
        msg = (
            f"🚨 <b>{title}</b>\n"
            f"時間: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"內容: <code>{message}</code>"
        )
        self.send(msg)

    def notify_mode_switch(self, mode: str) -> None:
        emoji = "🛡️" if mode == "conservative" else "⚡"
        label = "保守模式" if mode == "conservative" else "積極模式"
        self.send(f"{emoji} <b>切換至{label}</b>")

    def notify_halt(self, reason: str) -> None:
        self.send(f"🚨 <b>交易暫停</b>\n原因: {reason}")

    def notify_status(self, status: dict) -> None:
        grids = status.get("active_grids", [])
        grid_info = "\n".join(
            f"  · {g['symbol']} {g['direction']} {g['levels']}格 淨PnL ${g.get('net_pnl', 0)}"
            for g in grids
        ) or "  (無)"
        realized = status.get("session_realized_pnl", status.get("exchange_today_realized_pnl", 0))
        commission = status.get("session_commission", status.get("exchange_today_commission", 0))
        net_pnl = status.get("session_net_pnl", status.get("daily_profit", 0))
        negative_fills = status.get("session_negative_fills", [])
        negative_lines = []
        if negative_fills:
            negative_lines.append("負PnL 成交:")
            for fill in negative_fills:
                negative_lines.append(
                    f"  · {fill.get('timestamp', '?')} {fill.get('symbol', '?')} {fill.get('side', '?')} "
                    f"qty={float(fill.get('quantity', 0.0) or 0.0):.3f} "
                    f"pnl={float(fill.get('realized_pnl', 0.0) or 0.0):+.5f}"
                )
        else:
            negative_lines.append("負PnL 成交: 無")

        msg = (
            f"📊 <b>Jackbot 狀態</b>\n"
            f"模式: {status.get('mode', '?')}\n"
            f"策略: {status.get('strategy_variant', '?')}\n"
            f"Safe mode: {status.get('safe_mode', False)} {status.get('safe_mode_reason', '')}\n"
            f"目前權益: <b>${status.get('equity', 0):.2f}</b>\n"
            f"⚡ <i>以下數據自本次 Docker 啟動後起算</i>\n"
            f"  已實現: ${float(realized):+.4f}\n"
            f"  手續費: ${float(commission):.6f}\n"
            f"  淨盈餘: ${float(net_pnl):+.4f}\n"
            f"最後成交: {status.get('session_last_fill_at') or status.get('exchange_last_fill_at') or '-'}\n"
            f"日目標: ${status.get('daily_target', 0):.2f}\n"
            f"交易所掛單: {len(status.get('exchange_open_orders', []))} "
            f"(orphan {len(status.get('exchange_orphan_orders', []))})\n"
            f"交易所倉位: {len(status.get('exchange_orphan_positions', []))} orphan\n"
            f"活躍網格:\n{grid_info}\n"
            f"{chr(10).join(negative_lines)}"
        )
        self.send(msg)
