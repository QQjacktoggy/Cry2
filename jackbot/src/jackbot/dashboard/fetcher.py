"""Binance testnet data fetcher for the Jackbot dashboard."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import structlog

from jackbot.exchange.client import BinanceClient

logger = structlog.get_logger(__name__)


class DataFetcher:
    def __init__(self, symbols: list[str]) -> None:
        key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        self._client = BinanceClient(api_key=key, api_secret=secret, testnet=True)
        self._symbols = symbols
        try:
            self._client.load_symbol_info(symbols)
        except Exception as e:
            logger.warning("symbol_info_load_failed", error=str(e))

    def fetch(self) -> dict:
        latency = -1.0
        try:
            latency = self._client.ping()
        except Exception:
            pass

        balance = 0.0
        try:
            balance = self._client.get_balance()
        except Exception:
            pass

        symbols_data: dict = {}
        for sym in self._symbols:
            symbols_data[sym] = self._fetch_symbol(sym)

        return {
            "ts": datetime.now(UTC).isoformat(),
            "latency_ms": latency,
            "balance": balance,
            "symbols": symbols_data,
        }

    def _fetch_symbol(self, sym: str) -> dict:
        data: dict = {}

        # Current price
        try:
            data["price"] = self._client.get_ticker_price(sym)
        except Exception:
            data["price"] = 0.0

        # Klines (100 x 5m for chart)
        try:
            data["klines"] = self._client.get_klines(sym, "5m", 100)
        except Exception:
            data["klines"] = []

        # Position
        try:
            pos = self._client.get_position(sym)
            amt = float(pos.get("positionAmt", 0))
            data["position"] = {
                "side": "LONG" if amt > 0 else ("SHORT" if amt < 0 else "FLAT"),
                "qty": abs(amt),
                "entry": float(pos.get("entryPrice", 0)),
                "upnl": float(pos.get("unrealizedProfit", 0)),
                "leverage": int(float(pos.get("leverage", 1))),
                "liq_price": float(pos.get("liquidationPrice", 0)),
                "notional": abs(float(pos.get("notional", 0))),
            }
        except Exception:
            data["position"] = None

        # Open orders
        try:
            raw = self._client.get_open_orders(sym)
            data["orders"] = [
                {
                    "id": str(o.get("orderId", "")),
                    "side": o.get("side", ""),
                    "type": o.get("type", "LIMIT"),
                    "price": float(o.get("price", 0)),
                    "qty": float(o.get("origQty", 0)),
                    "filled": float(o.get("executedQty", 0)),
                    "status": o.get("status", ""),
                    "cid": o.get("clientOrderId", ""),
                }
                for o in raw
            ]
        except Exception:
            data["orders"] = []

        # Recent fills (last 20)
        try:
            fills = self._client._signed_get(
                "/fapi/v1/userTrades", {"symbol": sym, "limit": 20}
            )
            data["fills"] = [
                {
                    "time": f["time"],
                    "side": f.get("side", ""),
                    "price": float(f.get("price", 0)),
                    "qty": float(f.get("qty", 0)),
                    "pnl": float(f.get("realizedPnl", 0)),
                    "maker": bool(f.get("maker", False)),
                    "commission": float(f.get("commission", 0)),
                }
                for f in fills
            ]
        except Exception:
            data["fills"] = []

        return data

    def close(self) -> None:
        self._client.close()
