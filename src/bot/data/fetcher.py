"""Historical data download from Binance REST API."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import structlog

from bot.core.constants import BINANCE_FUTURES_BASE_URL, BINANCE_FUTURES_TESTNET_URL
from bot.core.exceptions import DataError
from bot.data.schemas import KLINE_COLUMNS, FUNDING_COLUMNS
from bot.utils.retry import retry_with_backoff

logger = structlog.get_logger(__name__)

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import urllib.request
import json


class BinanceFetcher:
    """Downloads historical K-line and funding rate data from Binance."""

    def __init__(
        self,
        base_url: str = BINANCE_FUTURES_BASE_URL,
        rate_limit_delay: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limit_delay = rate_limit_delay

    def _request(self, endpoint: str, params: dict[str, Any]) -> Any:
        """Make a GET request to the Binance API."""
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.base_url}{endpoint}?{query}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            raise DataError(f"API request failed: {url} - {e}") from e

    @retry_with_backoff(max_retries=3, base_delay=1.0, exceptions=(DataError,))
    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """Fetch K-lines from Binance API.

        Args:
            symbol: Trading pair (e.g., BTCUSDT).
            interval: Timeframe (1m, 5m, 15m, 1h, 4h, 1d).
            start_time: Start time in milliseconds.
            end_time: End time in milliseconds.
            limit: Max number of candles (max 1500).

        Returns:
            DataFrame with KLINE_COLUMNS schema.
        """
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = self._request("/fapi/v1/klines", params)

        if not data:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        rows = []
        for k in data:
            rows.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
                "trade_count": int(k[8]),
                "taker_buy_volume": float(k[9]),
                "taker_buy_quote_volume": float(k[10]),
            })

        return pd.DataFrame(rows, columns=KLINE_COLUMNS)

    def fetch_all_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        """Fetch all K-lines in a date range using pagination.

        Args:
            symbol: Trading pair.
            interval: Timeframe.
            start_time: Start time in milliseconds.
            end_time: End time in milliseconds (default: now).

        Returns:
            Complete DataFrame with all candles.
        """
        if end_time is None:
            end_time = int(time.time() * 1000)

        all_data: list[pd.DataFrame] = []
        current_start = start_time

        logger.info(
            "fetching_klines",
            symbol=symbol,
            interval=interval,
            start=current_start,
            end=end_time,
        )

        while current_start < end_time:
            df = self.fetch_klines(
                symbol=symbol,
                interval=interval,
                start_time=current_start,
                end_time=end_time,
                limit=1500,
            )

            if df.empty:
                break

            all_data.append(df)
            current_start = int(df["timestamp"].max()) + 1
            time.sleep(self.rate_limit_delay)

            logger.debug(
                "klines_batch",
                symbol=symbol,
                rows=len(df),
                last_ts=current_start,
            )

        if not all_data:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        result = pd.concat(all_data, ignore_index=True)
        result = result.drop_duplicates(subset=["timestamp"], keep="last")
        result = result.sort_values("timestamp").reset_index(drop=True)

        logger.info("klines_fetched", symbol=symbol, total_rows=len(result))
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, exceptions=(DataError,))
    def fetch_funding_rate(
        self,
        symbol: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch funding rate history."""
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = self._request("/fapi/v1/fundingRate", params)

        if not data:
            return pd.DataFrame(columns=FUNDING_COLUMNS)

        rows = []
        for item in data:
            rows.append({
                "timestamp": int(item["fundingTime"]),
                "symbol": item["symbol"],
                "funding_rate": float(item["fundingRate"]),
            })

        return pd.DataFrame(rows, columns=FUNDING_COLUMNS)

    def fetch_exchange_info(self) -> dict[str, Any]:
        """Fetch exchange info (contract specifications)."""
        return self._request("/fapi/v1/exchangeInfo", {})
