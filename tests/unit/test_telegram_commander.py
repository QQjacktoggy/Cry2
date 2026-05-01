from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.monitoring.telegram_commander import TelegramCommander


class _DummyNotifier:
    def __init__(self) -> None:
        self.chat_id = "123"
        self.messages: list[str] = []
        self._bot = object()

    async def send_message(self, text: str) -> None:
        self.messages.append(text)


class _DummyJournal:
    def __init__(self, fills: pd.DataFrame | None = None) -> None:
        self._fills = fills if fills is not None else pd.DataFrame()

    def summary(self) -> dict[str, float | int]:
        return {"total_trades": 2, "win_rate": 0.5}

    def export_fills(self, since=None):  # noqa: ANN001
        return self._fills


async def _build_commander(reset_callback=None, journal=None, started_at=None) -> TelegramCommander:
    return TelegramCommander(
        notifier=_DummyNotifier(),
        portfolio=SimpleNamespace(equity=150.0, cash=150.0, unrealized_pnl=0.0, realized_pnl=0.0),
        journal=journal or _DummyJournal(),
        initial_capital=150.0,
        started_at=started_at,
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


def test_status_shows_session_negative_pnl() -> None:
    started_at = datetime.now(UTC) - timedelta(hours=1)
    fills = pd.DataFrame(
        [
            {
                "timestamp": started_at + timedelta(minutes=10),
                "symbol": "ETHUSDC",
                "side": "BUY",
                "quantity": 0.066,
                "price": 2259.07,
                "realized_pnl": -0.30888,
                "commission": 0.06709437,
                "order_id": "288969935",
                "client_order_id": "jb_grid_ETHUSDC_ed7ee81f_05_B_99",
            },
            {
                "timestamp": started_at + timedelta(minutes=12),
                "symbol": "ETHUSDC",
                "side": "SELL",
                "quantity": 0.066,
                "price": 2257.73,
                "realized_pnl": 0.0,
                "commission": 0.0267,
                "order_id": "288969960",
                "client_order_id": "jb_grid_ETHUSDC_63b49059_05_B_00",
            },
        ]
    )

    commander = asyncio.run(_build_commander(journal=_DummyJournal(fills), started_at=started_at))
    status = asyncio.run(commander._cmd_status())

    assert "啟動後已實現(未扣手續費)" in status
    assert status.count("負PnL 成交") == 1
    assert "288969935" in status
    assert "pnl=-0.30888" in status
    assert "完整交易" in status
    assert "勝率" in status


def test_status_zero_fills_does_not_duplicate_negative_pnl_label() -> None:
    commander = asyncio.run(_build_commander(journal=_DummyJournal()))

    status = asyncio.run(commander._cmd_status())

    assert status.count("負PnL 成交") == 1
    assert "負PnL 成交: 無" not in status
    assert "目前權益(Paper)" in status
    assert "未實現損益" in status
