"""Binance Futures REST API wrapper.

Wraps python-binance for futures operations.
All methods include rate limiting and error handling.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from bot.core.exceptions import ExchangeError, RateLimitError
from bot.core.types import Position
from bot.exchange.rate_limiter import RateLimiter
from bot.exchange.testnet import EndpointConfig
from bot.utils.retry import retry_with_backoff

logger = structlog.get_logger(__name__)


class BinanceRestClient:
    """Binance Futures REST API client."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        mode: str = "testnet",
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.endpoint = EndpointConfig(mode)
        self.rate_limiter = rate_limiter or RateLimiter()
        self._client: Any = None

        try:
            from binance.client import Client
            self._client = Client(
                api_key=api_key,
                api_secret=api_secret,
                testnet=self.endpoint.is_testnet,
            )
            logger.info("binance_client_initialized", mode=mode)
        except ImportError:
            logger.warning("python-binance not installed, using mock mode")
        except Exception as e:
            raise ExchangeError(f"Failed to initialize Binance client: {e}") from e

    @retry_with_backoff(max_retries=3, base_delay=1.0, exceptions=(ExchangeError,))
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        post_only: bool = False,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Place a futures order.

        Returns:
            Order response dict from Binance.
        """
        self.rate_limiter.acquire(weight=1)

        if self._client is None:
            raise ExchangeError("Binance client not initialized")

        try:
            params: dict[str, Any] = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
            }

            if price is not None and order_type in ("LIMIT", "STOP"):
                params["price"] = str(price)
                params["timeInForce"] = "GTX" if post_only else "GTC"

            if stop_price is not None:
                params["stopPrice"] = str(stop_price)

            if reduce_only:
                params["reduceOnly"] = "true"

            if client_order_id:
                params["newClientOrderId"] = client_order_id

            result = self._client.futures_create_order(**params)
            logger.info(
                "order_placed",
                symbol=symbol,
                side=side,
                type=order_type,
                qty=quantity,
                order_id=result.get("orderId"),
            )
            return result

        except Exception as e:
            error_msg = str(e)
            if "1015" in error_msg or "rate" in error_msg.lower():
                raise RateLimitError() from e
            raise ExchangeError(f"Order failed: {error_msg}") from e

    @retry_with_backoff(max_retries=3, exceptions=(ExchangeError,))
    def cancel_order(self, symbol: str, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
        """Cancel an open order."""
        self.rate_limiter.acquire(weight=1)

        if self._client is None:
            raise ExchangeError("Client not initialized")

        try:
            params: dict[str, Any] = {"symbol": symbol}
            if order_id:
                params["orderId"] = order_id
            if client_order_id:
                params["origClientOrderId"] = client_order_id

            return self._client.futures_cancel_order(**params)
        except Exception as e:
            raise ExchangeError(f"Cancel failed: {e}") from e

    def get_position(self, symbol: str) -> Position:
        """Get current position for a symbol."""
        self.rate_limiter.acquire(weight=5)

        if self._client is None:
            return Position(symbol=symbol)

        try:
            positions = self._client.futures_position_information(symbol=symbol)
            for p in positions:
                qty = float(p.get("positionAmt", 0))
                if qty != 0:
                    from bot.core.constants import PositionSide
                    side = PositionSide.LONG if qty > 0 else PositionSide.SHORT
                    return Position(
                        symbol=symbol,
                        side=side,
                        quantity=abs(qty),
                        entry_price=float(p.get("entryPrice", 0)),
                        current_price=float(p.get("markPrice", 0)),
                        unrealized_pnl=float(p.get("unRealizedProfit", 0)),
                        leverage=int(p.get("leverage", 1)),
                    )
            return Position(symbol=symbol)
        except Exception as e:
            raise ExchangeError(f"Get position failed: {e}") from e

    def get_balance(self) -> float:
        """Get USDT available balance."""
        self.rate_limiter.acquire(weight=5)

        if self._client is None:
            return 0.0

        try:
            account = self._client.futures_account()
            for asset in account.get("assets", []):
                if asset["asset"] == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            raise ExchangeError(f"Get balance failed: {e}") from e

    def get_account_info(self) -> dict[str, Any]:
        """Get full account information."""
        self.rate_limiter.acquire(weight=5)

        if self._client is None:
            return {}

        try:
            return self._client.futures_account()
        except Exception as e:
            raise ExchangeError(f"Get account info failed: {e}") from e

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage for a symbol."""
        self.rate_limiter.acquire(weight=1)

        if self._client is None:
            return {}

        try:
            return self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            raise ExchangeError(f"Set leverage failed: {e}") from e

    def get_server_time(self) -> int:
        """Get Binance server time in milliseconds."""
        if self._client is None:
            return int(time.time() * 1000)
        try:
            result = self._client.futures_time()
            return int(result.get("serverTime", time.time() * 1000))
        except Exception:
            return int(time.time() * 1000)

    def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Get current funding rate for a symbol.

        Returns:
            Dict with 'symbol', 'markPrice', 'lastFundingRate', 'nextFundingTime'.
        """
        self.rate_limiter.acquire(weight=1)

        if self._client is None:
            return {"symbol": symbol, "lastFundingRate": "0", "nextFundingTime": 0}

        try:
            result = self._client.futures_mark_price(symbol=symbol)
            return result
        except Exception as e:
            raise ExchangeError(f"Get funding rate failed: {e}") from e

    def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get historical funding rates for a symbol.

        Returns:
            List of funding rate entries.
        """
        self.rate_limiter.acquire(weight=1)

        if self._client is None:
            return []

        try:
            params: dict[str, Any] = {"symbol": symbol, "limit": limit}
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time
            return self._client.futures_funding_rate(**params)
        except Exception as e:
            raise ExchangeError(f"Get funding history failed: {e}") from e

    def get_account_trades(
        self,
        symbol: str,
        limit: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent account trades (fills) for a symbol.

        Calls ``/fapi/v1/userTrades``.  Used by TradeJournal.reconcile()
        to backfill fills missed during a disconnect.

        Returns:
            List of trade dicts with keys: symbol, id, orderId, side,
            price, qty, realizedPnl, commission, commissionAsset, time.
        """
        self.rate_limiter.acquire(weight=5)

        if self._client is None:
            return []

        try:
            params: dict[str, Any] = {"symbol": symbol, "limit": limit}
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time
            return self._client.futures_account_trades(**params)
        except Exception as e:
            raise ExchangeError(f"Get account trades failed: {e}") from e

    def get_listen_key(self) -> str:
        """Create a new user data stream listen key (futures).

        Returns:
            The listen key string used to authenticate the user data WebSocket.
        """
        self.rate_limiter.acquire(weight=1)
        if self._client is None:
            return ""
        try:
            resp = self._client.futures_stream_get_listen_key()
            return resp.get("listenKey", "")
        except Exception as e:
            raise ExchangeError(f"Get listen key failed: {e}") from e

    def keep_alive_listen_key(self, listen_key: str) -> None:
        """Ping/extend a futures listen key (must be called every ~30 min).

        Args:
            listen_key: The listen key to keep alive.
        """
        self.rate_limiter.acquire(weight=1)
        if self._client is None:
            return
        try:
            self._client.futures_stream_keepalive(listenKey=listen_key)
        except Exception as e:
            raise ExchangeError(f"Keep-alive listen key failed: {e}") from e

    def ping(self) -> float:
        """Ping the API and return latency in ms."""
        start = time.time()
        self.get_server_time()
        return (time.time() - start) * 1000
