"""Portfolio tracker for Jackbot_V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TradeRecord:
    """Record of a completed grid match (buy + sell)."""

    timestamp: datetime
    symbol: str
    grid_id: str
    buy_price: float
    sell_price: float
    quantity: float
    profit_usd: float
    leverage: int


class Portfolio:
    """Tracks capital, trades, and daily PnL."""

    def __init__(self, initial_capital: float = 150.0) -> None:
        self._initial_capital = initial_capital
        self._available_capital = initial_capital
        self._trades: list[TradeRecord] = []
        self._daily_pnl: dict[str, float] = {}  # date_str → cumulative PnL

    @property
    def available_capital(self) -> float:
        return self._available_capital

    @property
    def total_pnl(self) -> float:
        return sum(t.profit_usd for t in self._trades)

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed grid match."""
        self._trades.append(trade)
        self._available_capital += trade.profit_usd

        date_key = trade.timestamp.strftime("%Y-%m-%d")
        self._daily_pnl[date_key] = self._daily_pnl.get(date_key, 0.0) + trade.profit_usd

        logger.info(
            "trade_recorded",
            symbol=trade.symbol,
            profit=round(trade.profit_usd, 4),
            capital=round(self._available_capital, 2),
        )

    def get_daily_pnl(self, date_str: str | None = None) -> float:
        if date_str is None:
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._daily_pnl.get(date_str, 0.0)

    def get_summary(self) -> dict:
        return {
            "initial_capital": self._initial_capital,
            "available_capital": round(self._available_capital, 2),
            "total_pnl": round(self.total_pnl, 4),
            "total_trades": len(self._trades),
            "today_pnl": round(self.get_daily_pnl(), 4),
        }
