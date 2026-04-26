"""Binance Futures REST API client for Jackbot_V1.

Supports both testnet and mainnet. Designed for grid trading:
  - Limit order placement and cancellation
  - Isolated margin leverage setting
  - Position and balance queries
  - Historical klines for warmup
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Testnet and production base URLs
TESTNET_URL = "https://testnet.binancefuture.com"
MAINNET_URL = "https://fapi.binance.com"


class BinanceClient:
    """Binance USDT-M Futures REST API wrapper."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        base_url: str = "",
    ) -> None:
        self._api_key = api_key or os.getenv("BINANCE_TESTNET_API_KEY", "")
        self._api_secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET", "")
        self._base_url = base_url or (TESTNET_URL if testnet else MAINNET_URL)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=10.0,
            headers={"X-MBX-APIKEY": self._api_key},
        )
        # symbol → (qty_precision, price_precision); populated by load_symbol_info()
        self._qty_precision: dict[str, int] = {}
        self._price_precision: dict[str, int] = {}

    def close(self) -> None:
        self._client.close()

    # ── Market Data (public) ──────────────────────────────────────────

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list[dict]:
        """Fetch historical klines (candlesticks).

        Returns list of dicts with keys: timestamp, open, high, low, close, volume.
        """
        resp = self._client.get(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()

        klines = []
        for row in data:
            klines.append({
                "timestamp": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        return klines

    def get_ticker_price(self, symbol: str) -> float:
        """Get latest price for a symbol."""
        resp = self._client.get("/fapi/v1/ticker/price", params={"symbol": symbol})
        resp.raise_for_status()
        return float(resp.json()["price"])

    def load_symbol_info(self, symbols: list[str]) -> None:
        """Fetch and cache qty/price precision for each symbol from exchange info."""
        resp = self._client.get("/fapi/v1/exchangeInfo")
        resp.raise_for_status()
        for sym_info in resp.json().get("symbols", []):
            if sym_info["symbol"] in symbols:
                self._qty_precision[sym_info["symbol"]] = sym_info["quantityPrecision"]
                self._price_precision[sym_info["symbol"]] = sym_info["pricePrecision"]
                logger.info(
                    "symbol_info_loaded",
                    symbol=sym_info["symbol"],
                    qty_precision=sym_info["quantityPrecision"],
                    price_precision=sym_info["pricePrecision"],
                    margin_asset=sym_info.get("marginAsset"),
                )

    def ping(self) -> float:
        """Test connectivity and measure latency (ms)."""
        start = time.monotonic()
        resp = self._client.get("/fapi/v1/ping")
        resp.raise_for_status()
        return round((time.monotonic() - start) * 1000, 1)

    # ── Account (signed) ──────────────────────────────────────────────

    def get_balance(self) -> float:
        """Get USDT available balance."""
        data = self._signed_get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
        return 0.0

    def get_position(self, symbol: str) -> dict:
        """Get position info for a symbol."""
        data = self._signed_get("/fapi/v2/positionRisk", {"symbol": symbol})
        if data:
            return data[0]
        return {}

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set isolated margin leverage for a symbol."""
        try:
            self._signed_post("/fapi/v1/leverage", {
                "symbol": symbol,
                "leverage": leverage,
            })
            logger.info("leverage_set", symbol=symbol, leverage=leverage)
        except Exception as e:
            logger.warning("leverage_set_failed", symbol=symbol, error=str(e))

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> None:
        """Set margin type (ISOLATED / CROSSED)."""
        try:
            self._signed_post("/fapi/v1/marginType", {
                "symbol": symbol,
                "marginType": margin_type,
            })
            logger.info("margin_type_set", symbol=symbol, margin_type=margin_type)
        except Exception as e:
            # Ignore "No need to change margin type" error
            if "-4046" not in str(e):
                logger.warning("margin_type_failed", symbol=symbol, error=str(e))

    # ── Orders ────────────────────────────────────────────────────────

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        client_order_id: str = "",
    ) -> dict:
        """Place a limit order."""
        qty_prec = self._qty_precision.get(symbol, 3)
        price_prec = self._price_precision.get(symbol, 2)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "price": f"{price:.{price_prec}f}",
            "quantity": f"{quantity:.{qty_prec}f}",
            "timeInForce": "GTC",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        result = self._signed_post("/fapi/v1/order", params)
        logger.info(
            "limit_order_placed",
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            order_id=result.get("orderId"),
        )
        return result

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> dict:
        """Place a market order."""
        qty_prec = self._qty_precision.get(symbol, 3)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{quantity:.{qty_prec}f}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        result = self._signed_post("/fapi/v1/order", params)
        logger.info(
            "market_order_placed",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_id=result.get("orderId"),
        )
        return result

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel a specific order."""
        try:
            self._signed_delete("/fapi/v1/order", {
                "symbol": symbol,
                "orderId": order_id,
            })
            logger.info("order_cancelled", symbol=symbol, order_id=order_id)
            return True
        except Exception as e:
            logger.warning("cancel_failed", symbol=symbol, order_id=order_id, error=str(e))
            return False

    def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for a symbol. Returns count of cancelled orders."""
        try:
            self._signed_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
            logger.info("all_orders_cancelled", symbol=symbol)
            return 1  # API doesn't return count
        except Exception as e:
            logger.warning("cancel_all_failed", symbol=symbol, error=str(e))
            return 0

    def get_open_orders(self, symbol: str) -> list[dict]:
        """Get all open orders for a symbol."""
        return self._signed_get("/fapi/v1/openOrders", {"symbol": symbol})

    # ── Signing helpers ───────────────────────────────────────────────

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _signed_get(self, path: str, params: dict | None = None) -> Any:
        params = self._sign(params or {})
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _signed_post(self, path: str, params: dict) -> Any:
        params = self._sign(params)
        resp = self._client.post(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _signed_delete(self, path: str, params: dict) -> Any:
        params = self._sign(params)
        resp = self._client.delete(path, params=params)
        resp.raise_for_status()
        return resp.json()
