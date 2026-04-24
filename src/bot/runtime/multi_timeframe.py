from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

TIMEFRAME_ORDER = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "8h": 480, "1d": 1440}


def timeframe_rank(timeframe: str) -> int:
    return TIMEFRAME_ORDER.get(str(timeframe), 10**9)


def sort_timeframes(timeframes: Iterable[str]) -> list[str]:
    return sorted({str(timeframe) for timeframe in timeframes}, key=timeframe_rank)


def group_symbols_by_timeframe(strategies: Iterable[Any]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for strategy in strategies:
        timeframe = str(getattr(strategy, "timeframe", ""))
        symbol = str(getattr(strategy, "symbol", ""))
        if timeframe and symbol:
            grouped[timeframe].add(symbol)
    return {timeframe: sorted(symbols) for timeframe, symbols in grouped.items()}


def event_matches_strategy(strategy: Any, event: Any) -> bool:
    event_symbol = getattr(event, "symbol", None)
    event_timeframe = getattr(event, "timeframe", None)
    strategy_symbols = getattr(strategy, "symbols", []) or []
    strategy_timeframe = getattr(strategy, "timeframe", None)
    return event_symbol in strategy_symbols and event_timeframe == strategy_timeframe