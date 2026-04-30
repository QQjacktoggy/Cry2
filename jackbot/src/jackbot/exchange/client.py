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
from decimal import Decimal

logger = structlog.get_logger(__name__)


def _decimals(step: str) -> int:
    """Return number of decimal places implied by a step/tick string like '0.10' or '0.001'."""
    return abs(Decimal(step).normalize().as_tuple().exponent)


def _round_step(value: float, step: float) -> str:
    """Round value down to the nearest multiple of step, maintaining precision."""
    if not step:
        return str(value)
    # Use Decimal for exact precision
    d_step = Decimal(str(step))
    d_value = Decimal(str(value))
    rounded = (d_value // d_step) * d_step
    return f"{rounded.normalize():f}"


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
        self._tick_size: dict[str, float] = {}
        self._step_size: dict[str, float] = {}
        self._min_notional: dict[str, float] = {}

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

    def min_notional(self, symbol: str) -> float:
        """Return Binance's minimum order notional for a symbol, if known."""
        return self._min_notional.get(symbol, 0.0)

    def order_notional(self, symbol: str, price: float, quantity: float) -> float:
        """Compute notional using the limit price or the latest ticker for market orders."""
        ref_price = price if price > 0 else self.get_ticker_price(symbol)
        return ref_price * quantity

    def load_symbol_info(self, symbols: list[str]) -> None:
        """Fetch and cache qty/price precision from actual filter tick sizes.

        Uses LOT_SIZE.stepSize for qty and PRICE_FILTER.tickSize for price —
        these are the values Binance enforces, not the metadata pricePrecision
        field which can differ (e.g. BTCUSDT has pricePrecision=2 but tickSize=0.10).
        """
        resp = self._client.get("/fapi/v1/exchangeInfo")
        resp.raise_for_status()
        for sym_info in resp.json().get("symbols", []):
            if sym_info["symbol"] not in symbols:
                continue
            filters = {f["filterType"]: f for f in sym_info.get("filters", [])}
            tick = filters.get("PRICE_FILTER", {}).get("tickSize", "0.01")
            step = filters.get("LOT_SIZE", {}).get("stepSize", "0.001")
            min_notional = (
                filters.get("MIN_NOTIONAL", {}).get("notional")
                or filters.get("NOTIONAL", {}).get("minNotional")
                or "0"
            )
            self._price_precision[sym_info["symbol"]] = _decimals(tick)
            self._qty_precision[sym_info["symbol"]] = _decimals(step)
            self._tick_size[sym_info["symbol"]] = float(tick)
            self._step_size[sym_info["symbol"]] = float(step)
            self._min_notional[sym_info["symbol"]] = float(min_notional)
            logger.info(
                "symbol_info_loaded",
                symbol=sym_info["symbol"],
                price_precision=self._price_precision[sym_info["symbol"]],
                qty_precision=self._qty_precision[sym_info["symbol"]],
                tick_size=tick,
                step_size=step,
                min_notional=min_notional,
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

    def get_account_info(self) -> dict:
        """Get full account information including assets and positions."""
        return self._signed_get("/fapi/v2/account")

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
        tick = self._tick_size.get(symbol, 0.01)
        step = self._step_size.get(symbol, 0.001)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "price": _round_step(price, tick),
            "quantity": _round_step(quantity, step),
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
        step = self._step_size.get(symbol, 0.001)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": _round_step(quantity, step),
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
        except httpx.HTTPStatusError as e:
            # -2011: "Unknown order" (likely already closed/filled)
            try:
                error_data = e.response.json()
                if error_data.get("code") == -2011:
                    logger.debug("cancel_ignored_already_closed", symbol=symbol, order_id=order_id)
                    return True
            except Exception:
                pass
            
            logger.warning("cancel_failed", symbol=symbol, order_id=order_id, error=str(e))
            return False
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

    # ── User Data Stream (listenKey) ──────────────────────────────────

    def get_listen_key(self) -> str:
        """Generate a new listenKey for user data stream."""
        resp = self._client.post("/fapi/v1/listenKey")
        resp.raise_for_status()
        return resp.json().get("listenKey", "")

    def keep_alive_listen_key(self) -> bool:
        """Extend the validity of the current listenKey."""
        try:
            resp = self._client.put("/fapi/v1/listenKey")
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("listen_key_keepalive_failed", error=str(e))
            return False

    def close_listen_key(self) -> bool:
        """Close the user data stream."""
        try:
            resp = self._client.delete("/fapi/v1/listenKey")
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("listen_key_close_failed", error=str(e))
            return False

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
