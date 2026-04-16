"""Account information - positions, balance, leverage management."""

from __future__ import annotations

from typing import Any

import structlog

from bot.core.types import Position
from bot.exchange.binance_rest import BinanceRestClient

logger = structlog.get_logger(__name__)


class AccountManager:
    """Manages account-level operations."""

    def __init__(self, client: BinanceRestClient) -> None:
        self.client = client

    def get_balance(self) -> float:
        """Get USDT available balance."""
        return self.client.get_balance()

    def get_equity(self) -> float:
        """Get total equity including unrealized PnL."""
        info = self.client.get_account_info()
        return float(info.get("totalWalletBalance", 0)) + float(info.get("totalUnrealizedProfit", 0))

    def get_position(self, symbol: str) -> Position:
        """Get position for a symbol."""
        return self.client.get_position(symbol)

    def get_all_positions(self) -> list[Position]:
        """Get all open positions."""
        info = self.client.get_account_info()
        positions = []
        for p in info.get("positions", []):
            qty = float(p.get("positionAmt", 0))
            if qty != 0:
                from bot.core.constants import PositionSide
                positions.append(Position(
                    symbol=p["symbol"],
                    side=PositionSide.LONG if qty > 0 else PositionSide.SHORT,
                    quantity=abs(qty),
                    entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedProfit", 0)),
                    leverage=int(p.get("leverage", 1)),
                ))
        return positions

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol."""
        self.client.set_leverage(symbol, leverage)
        logger.info("leverage_set", symbol=symbol, leverage=leverage)

    def get_margin_ratio(self) -> float:
        """Get current margin ratio (equity / maintenance margin)."""
        info = self.client.get_account_info()
        maintenance = float(info.get("totalMaintMargin", 0))
        equity = float(info.get("totalMarginBalance", 0))
        if maintenance <= 0:
            return 100.0
        return (equity / maintenance) * 100
