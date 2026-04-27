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
    """Tracks capital, trades, and daily PnL with virtual equity support."""

    def __init__(self, initial_capital: float = 150.0) -> None:
        self._initial_capital = initial_capital
        self._realized_pnl = 0.0
        self._unrealized_pnl = 0.0
        self._trades: list[TradeRecord] = []
        self._daily_pnl: dict[str, float] = {}  # date_str -> cumulative PnL

    @property
    def initial_capital(self) -> float:
        return self._initial_capital

    @property
    def total_realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def unrealized_pnl(self) -> float:
        return self._unrealized_pnl

    @unrealized_pnl.setter
    def unrealized_pnl(self, value: float) -> None:
        self._unrealized_pnl = value

    @property
    def total_equity(self) -> float:
        """Total virtual equity: 150 + Realized + Unrealized."""
        return self._initial_capital + self._realized_pnl + self._unrealized_pnl

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed grid match."""
        self._trades.append(trade)
        self._realized_pnl += trade.profit_usd

        date_key = trade.timestamp.strftime("%Y-%m-%d")
        self._daily_pnl[date_key] = self._daily_pnl.get(date_key, 0.0) + trade.profit_usd

        logger.info(
            "trade_recorded",
            symbol=trade.symbol,
            profit=round(trade.profit_usd, 4),
            equity=round(self.total_equity, 2),
        )

    def get_summary(self) -> dict:
        return {
            "initial_capital": self._initial_capital,
            "realized_pnl": round(self._realized_pnl, 4),
            "unrealized_pnl": round(self._unrealized_pnl, 4),
            "total_equity": round(self.total_equity, 2),
            "total_trades": len(self._trades),
            "today_pnl": round(self._daily_pnl.get(datetime.now(UTC).strftime("%Y-%m-%d"), 0.0), 4),
        }

