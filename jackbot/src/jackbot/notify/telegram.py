"""Telegram notifications for Jackbot_V1."""

from __future__ import annotations

import os
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

    def notify_grid_profit(self, profit: float, total: float, target: float, equity: float = 0) -> None:
        pct = total / target * 100 if target > 0 else 0
        bar_len = min(20, int(pct / 5))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        equity_str = f"\n權益(含未實現): <b>${equity:.2f}</b>" if equity > 0 else ""
        msg = (
            f"💰 <b>格間利潤</b> +${profit:.4f}\n"
            f"日標進度: [{bar}] {pct:.1f}%\n"
            f"累計獲利: ${total:.4f} / ${target:.2f}"
            f"{equity_str}"
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
            f"  · {g['symbol']} {g['direction']} {g['levels']}格 利潤${g['profit']}"
            for g in grids
        ) or "  (無)"

        msg = (
            f"📊 <b>Jackbot 狀態</b>\n"
            f"模式: {status.get('mode', '?')}\n"
            f"目前權益: <b>${status.get('equity', 0):.2f}</b>\n"
            f"日利潤: ${status.get('daily_profit', 0):.4f} / ${status.get('daily_target', 0):.2f}\n"
            f"活躍網格:\n{grid_info}"
        )
        self.send(msg)
