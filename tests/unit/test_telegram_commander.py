from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.monitoring.telegram_commander import TelegramCommander


class _DummyNotifier:
    def __init__(self) -> None:
        self.chat_id = "123"
        self.messages: list[str] = []
        self._bot = object()

    async def send_message(self, text: str) -> None:
        self.messages.append(text)


async def _build_commander(reset_callback=None) -> TelegramCommander:
    return TelegramCommander(
        notifier=_DummyNotifier(),
        portfolio=SimpleNamespace(equity=150.0, cash=150.0, unrealized_pnl=0.0, realized_pnl=0.0),
        journal=SimpleNamespace(summary=lambda: {}, export_fills=lambda: SimpleNamespace(empty=True)),
        initial_capital=150.0,
        started_at=None,
        get_version=lambda: "v74",
        stop_event=asyncio.Event(),
        strategies=[],
        reset_callback=reset_callback,
    )


def test_reset_requires_confirm() -> None:
    async def _reset_callback() -> str:
        return "reset requested"

    commander = asyncio.run(_build_commander(reset_callback=_reset_callback))

    asyncio.run(commander._dispatch("/reset", "123"))

    notifier = commander._notifier
    assert len(notifier.messages) == 1
    assert "/reset CONFIRM" in notifier.messages[0]


def test_reset_confirm_calls_callback() -> None:
    async def _reset_callback() -> str:
        return "reset requested"

    commander = asyncio.run(_build_commander(reset_callback=_reset_callback))

    asyncio.run(commander._dispatch("/reset CONFIRM", "123"))

    notifier = commander._notifier
    assert notifier.messages[-1] == "reset requested"