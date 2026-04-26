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

    def send(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        """Send a message to the configured chat."""
        if not self._enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload: dict[str, Any] = {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            self._client.post(url, json=payload)
        except Exception as e:
            logger.warning("telegram_send_failed", error=str(e))

    def send_long(self, text: str, chunk_size: int = 3500) -> None:
        """Send long content in multiple messages within Telegram limits."""
        if len(text) <= chunk_size:
            self.send(text)
            return

        start = 0
        while start < len(text):
            chunk = text[start:start + chunk_size]
            self.send(chunk)
            start += chunk_size

    def build_main_keyboard(self, page: str = "monitor") -> dict[str, Any]:
        """Paged inline keyboard for quick bot operations."""
        page = (page or "monitor").lower()

        nav_row = [
            {"text": "監控頁", "callback_data": "page:monitor"},
            {"text": "交易頁", "callback_data": "page:trading"},
            {"text": "系統頁", "callback_data": "page:system"},
        ]

        monitor_rows = [
            [
                {"text": "Status", "callback_data": "cmd:status"},
                {"text": "Report", "callback_data": "cmd:report"},
            ],
            [
                {"text": "Balance", "callback_data": "cmd:balance"},
                {"text": "Runtime", "callback_data": "cmd:runtime"},
            ],
            [
                {"text": "Positions", "callback_data": "cmd:positions"},
                {"text": "Orders", "callback_data": "cmd:orders"},
            ],
            [
                {"text": "Recent", "callback_data": "cmd:recent"},
                {"text": "GCP", "callback_data": "cmd:gcp"},
            ],
        ]

        trading_rows = [
            [
                {"text": "HALT", "callback_data": "cmd:halt"},
                {"text": "RESUME", "callback_data": "cmd:resume"},
            ],
            [
                {"text": "Mode: Aggressive", "callback_data": "cmd:mode aggressive"},
                {"text": "Mode: Conservative", "callback_data": "cmd:mode conservative"},
            ],
            [
                {"text": "Close All", "callback_data": "danger:closeall"},
                {"text": "Orders", "callback_data": "cmd:orders"},
            ],
        ]

        system_rows = [
            [
                {"text": "GCP", "callback_data": "cmd:gcp"},
                {"text": "Runtime", "callback_data": "cmd:runtime"},
            ],
            [
                {"text": "Ping", "callback_data": "cmd:ping"},
                {"text": "Full Report", "callback_data": "cmd:report"},
            ],
            [
                {"text": "Shutdown", "callback_data": "danger:shutdown"},
            ],
        ]

        page_rows_map = {
            "monitor": monitor_rows,
            "trading": trading_rows,
            "system": system_rows,
        }
        rows = page_rows_map.get(page, monitor_rows)
        rows.append(nav_row)
        return {"inline_keyboard": rows}

    def build_confirm_keyboard(self, action: str) -> dict[str, Any]:
        """Confirmation keyboard for dangerous operations."""
        return {
            "inline_keyboard": [
                [
                    {"text": f"Confirm {action}", "callback_data": f"confirm:{action}"},
                    {"text": "Cancel", "callback_data": "cancel:danger"},
                ],
            ]
        }

    def send_menu(self, page: str = "monitor") -> None:
        """Send paged inline keyboard menu."""
        titles = {
            "monitor": "📊 <b>Jackbot 監控頁</b>",
            "trading": "📈 <b>Jackbot 交易頁</b>",
            "system": "☁️ <b>Jackbot 系統頁</b>",
        }
        page_key = (page or "monitor").lower()
        title = titles.get(page_key, titles["monitor"])
        self.send(title, reply_markup=self.build_main_keyboard(page=page_key))

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

    def notify_grid_profit(self, profit: float, total: float, target: float) -> None:
        pct = total / target * 100 if target > 0 else 0
        bar_len = int(pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        msg = (
            f"💰 <b>格間利潤</b> +${profit:.4f}\n"
            f"日標進度: [{bar}] {pct:.1f}%\n"
            f"累計: ${total:.4f} / ${target:.2f}"
        )
        self.send(msg)

    def notify_mode_switch(self, mode: str) -> None:
        emoji = "🛡️" if mode == "conservative" else "⚡"
        label = "保守模式" if mode == "conservative" else "積極模式"
        self.send(f"{emoji} <b>切換至{label}</b>")

    def notify_halt(self, reason: str) -> None:
        self.send(f"🚨 <b>交易暫停</b>\n原因: {reason}")

    def notify_error(self, title: str, error: str) -> None:
        self.send(
            f"❌ <b>{title}</b>\n"
            f"<code>{error[:800]}</code>"
        )

    def notify_report(self, report: str) -> None:
        self.send_long(report)

    def notify_status(self, status: dict) -> None:
        grids = status.get("active_grids", [])
        grid_info = "\n".join(
            f"  · {g['symbol']} {g['direction']} {g['levels']}格 利潤${g['profit']}"
            for g in grids
        ) or "  (無)"

        msg = (
            f"📊 <b>Jackbot 狀態</b>\n"
            f"模式: {status.get('mode', '?')}\n"
            f"日利潤: ${status.get('daily_profit', 0):.4f} / ${status.get('daily_target', 0):.2f}\n"
            f"活躍網格:\n{grid_info}"
        )
        self.send(msg)
