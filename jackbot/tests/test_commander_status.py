"""Tests for Telegram commander status formatting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from jackbot.notify.commander import JackbotCommander


class _DummyBot:
    _enabled = False
    _chat_id = "123"

    def send(self, message: str) -> None:
        self.last_message = message


class _DummyPortfolio:
    def get_summary(self) -> dict[str, float]:
        return {
            "initial_capital": 150.0,
            "realized_pnl": 0.0,
            "total_commission": 0.0,
            "net_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_equity": 150.0,
        }


@pytest.mark.asyncio
async def test_status_prefers_exchange_pnl_over_paper_portfolio() -> None:
    status = {
        "strategy_variant": "baseline_grid",
        "mode": "aggressive",
        "safe_mode": False,
        "safe_mode_reason": "",
        "active_grids": [],
        "exchange_repair_plan": {},
        "exchange_open_orders": [{}, {}, {}, {}],
        "exchange_orphan_orders": [],
        "exchange_orphan_positions": [],
        "exchange_today_realized_pnl": 0.082,
        "exchange_today_commission": 0.011,
        "exchange_today_fills": 29,
        "session_negative_fill_count": 0,
        "session_negative_fills": [],
    }
    commander = JackbotCommander(
        bot=_DummyBot(),
        trader=SimpleNamespace(get_status=lambda: {}),
        portfolio=_DummyPortfolio(),
        client=object(),
        stop_event=asyncio.Event(),
        journal=None,
        started_at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        status_provider=lambda: status,
    )

    output = await commander._cmd_status()

    assert "交易所已實現: $+0.0820 USDT" in output
    assert "交易所手續費: $0.011000 USDT" in output
    assert "交易所淨盈餘: $+0.0710 USDT" in output
    assert "交易所成交筆數: 29 筆" in output
    assert "Paper 權益" in output
    assert "• 已實現:" not in output
