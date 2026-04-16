"""Telegram notification service.

Sends trading alerts, errors, and system status to a Telegram chat.
Also supports receiving commands (e.g., remote kill switch).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    """Sends notifications via Telegram Bot API."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self._bot: Any = None

        if enabled and bot_token:
            try:
                from telegram import Bot
                self._bot = Bot(token=bot_token)
            except ImportError:
                logger.warning("python-telegram-bot not installed")
            except Exception as e:
                logger.error("telegram_init_failed", error=str(e))

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat."""
        if not self.enabled or not self._bot:
            logger.debug("telegram_disabled", message=text[:50])
            return False

        try:
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))
            return False

    def send_sync(self, text: str) -> bool:
        """Synchronous wrapper for sending messages."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send_message(text))
                return True
            return loop.run_until_complete(self.send_message(text))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.send_message(text))
            finally:
                loop.close()

    def notify_trade(self, symbol: str, side: str, quantity: float, price: float, pnl: float = 0.0) -> None:
        """Send trade notification."""
        emoji = "🟢" if side == "BUY" else "🔴"
        pnl_str = f"\nPnL: {pnl:+.2f} USDT" if pnl != 0 else ""
        text = f"{emoji} <b>{side} {symbol}</b>\nQty: {quantity}\nPrice: {price:.4f}{pnl_str}"
        self.send_sync(text)

    def notify_error(self, error: str, severity: str = "ERROR") -> None:
        """Send error notification."""
        self.send_sync(f"⚠️ <b>{severity}</b>\n{error}")

    def notify_kill_switch(self, reason: str) -> None:
        """Send kill switch notification."""
        self.send_sync(f"🚨 <b>KILL SWITCH ACTIVATED</b>\nReason: {reason}")

    def notify_circuit_breaker(self, reason: str, cooldown_min: int) -> None:
        """Send circuit breaker notification."""
        self.send_sync(f"⛔ <b>Circuit Breaker</b>\n{reason}\nCooldown: {cooldown_min} min")

    def notify_daily_summary(self, equity: float, daily_pnl: float, trades: int) -> None:
        """Send daily summary."""
        emoji = "📈" if daily_pnl >= 0 else "📉"
        text = (
            f"{emoji} <b>Daily Summary</b>\n"
            f"Equity: {equity:.2f} USDT\n"
            f"Daily PnL: {daily_pnl:+.2f} USDT\n"
            f"Trades: {trades}"
        )
        self.send_sync(text)
