from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.runtime.multi_timeframe import event_matches_strategy, group_symbols_by_timeframe, sort_timeframes


def test_sort_timeframes_orders_from_fast_to_slow() -> None:
    assert sort_timeframes(["1d", "4h", "4h", "1h"]) == ["1h", "4h", "1d"]


def test_group_symbols_by_timeframe_groups_unique_symbols() -> None:
    strategies = [
        SimpleNamespace(timeframe="4h", symbol="BTCUSDT"),
        SimpleNamespace(timeframe="1d", symbol="BNBUSDT"),
        SimpleNamespace(timeframe="4h", symbol="ETHUSDT"),
        SimpleNamespace(timeframe="4h", symbol="BTCUSDT"),
    ]

    assert group_symbols_by_timeframe(strategies) == {
        "1d": ["BNBUSDT"],
        "4h": ["BTCUSDT", "ETHUSDT"],
    }


def test_event_matches_strategy_requires_matching_timeframe() -> None:
    strategy = SimpleNamespace(symbols=["BNBUSDT"], timeframe="1d")
    four_hour_event = SimpleNamespace(symbol="BNBUSDT", timeframe="4h")
    daily_event = SimpleNamespace(symbol="BNBUSDT", timeframe="1d")

    assert event_matches_strategy(strategy, four_hour_event) is False
    assert event_matches_strategy(strategy, daily_event) is True