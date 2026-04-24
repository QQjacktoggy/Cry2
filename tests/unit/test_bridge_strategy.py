"""Tests for bridge strategy runtime state restoration."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.strategy.bridge import VBTBridgeStrategy


def test_restore_position_state_updates_runtime_state() -> None:
    strategy = VBTBridgeStrategy(
        vbt_strategy_name="tail_risk_hedge",
        params={"leverage": 1},
        symbol="SOLUSDT",
    )

    strategy.restore_position_state("long")
    assert strategy._in_position == "long"

    strategy.restore_position_state("flat")
    assert strategy._in_position == "flat"


def test_restore_position_state_ignores_invalid_values() -> None:
    strategy = VBTBridgeStrategy(
        vbt_strategy_name="tail_risk_hedge",
        params={"leverage": 1},
        symbol="SOLUSDT",
    )

    strategy.restore_position_state("long")
    strategy.restore_position_state("invalid")

    assert strategy._in_position == "long"