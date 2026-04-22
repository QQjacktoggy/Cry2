"""Portfolio management layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bot.portfolio.account_snapshot import SnapshotManager
from bot.portfolio.allocator import CapitalAllocator
from bot.portfolio.portfolio import Portfolio
from bot.portfolio.position import PositionTracker
from bot.portfolio.trade_journal import TradeJournal

if TYPE_CHECKING:
    from bot.portfolio.trade_analyzer import TradeAnalyzer

__all__ = [
    "Portfolio",
    "PositionTracker",
    "SnapshotManager",
    "CapitalAllocator",
    "TradeJournal",
    "TradeAnalyzer",
]


def __getattr__(name: str) -> Any:
    """Lazy-load TradeAnalyzer so runtime entrypoints don't require vectorbt."""
    if name == "TradeAnalyzer":
        from bot.portfolio.trade_analyzer import TradeAnalyzer as _TradeAnalyzer

        return _TradeAnalyzer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
