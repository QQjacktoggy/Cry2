"""Contract specification cache and utilities."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from bot.core.exceptions import DataError
from bot.core.types import SymbolInfo
from bot.data.fetcher import BinanceFetcher

logger = structlog.get_logger(__name__)


class SymbolInfoCache:
    """Caches and provides contract specifications.

    Fetches from Binance exchangeInfo API and caches locally.
    Refreshes daily.
    """

    def __init__(
        self,
        fetcher: BinanceFetcher,
        cache_dir: str = "./data/exchange_info",
        cache_ttl_hours: int = 24,
    ) -> None:
        self.fetcher = fetcher
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_hours = cache_ttl_hours
        self._cache: dict[str, SymbolInfo] = {}
        self._last_update: float = 0

    def _cache_file(self) -> Path:
        """Get today's cache file path."""
        date_str = time.strftime("%Y-%m-%d")
        return self.cache_dir / f"{date_str}.json"

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache:
            return False
        elapsed_hours = (time.time() - self._last_update) / 3600
        return elapsed_hours < self.cache_ttl_hours

    def refresh(self) -> None:
        """Refresh symbol info from API or local cache."""
        cache_file = self._cache_file()

        # Try local file cache first
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                self._parse_exchange_info(data)
                self._last_update = time.time()
                logger.info("symbol_info_loaded_from_cache", count=len(self._cache))
                return
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("cache_file_invalid", error=str(e))

        # Fetch from API
        try:
            data = self.fetcher.fetch_exchange_info()
            self._parse_exchange_info(data)
            self._last_update = time.time()

            # Save to local cache
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)

            logger.info("symbol_info_fetched", count=len(self._cache))
        except Exception as e:
            logger.error("symbol_info_fetch_failed", error=str(e))
            if not self._cache:
                raise DataError(f"Failed to load symbol info: {e}") from e

    def _parse_exchange_info(self, data: dict[str, Any]) -> None:
        """Parse exchangeInfo response into SymbolInfo objects."""
        symbols = data.get("symbols", [])
        for s in symbols:
            if s.get("contractType") != "PERPETUAL":
                continue
            if s.get("status") != "TRADING":
                continue

            filters = {f["filterType"]: f for f in s.get("filters", [])}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            min_notional_filter = filters.get("MIN_NOTIONAL", {})

            info = SymbolInfo(
                symbol=s["symbol"],
                base_asset=s.get("baseAsset", ""),
                quote_asset=s.get("quoteAsset", "USDT"),
                price_precision=int(s.get("pricePrecision", 2)),
                quantity_precision=int(s.get("quantityPrecision", 3)),
                tick_size=float(price_filter.get("tickSize", 0.01)),
                step_size=float(lot_filter.get("stepSize", 0.001)),
                min_quantity=float(lot_filter.get("minQty", 0.001)),
                min_notional=float(min_notional_filter.get("notional", 5.0)),
                max_leverage=125,
            )
            self._cache[info.symbol] = info

    def get(self, symbol: str) -> SymbolInfo:
        """Get symbol info, refreshing if needed."""
        if not self._is_cache_valid():
            self.refresh()

        if symbol not in self._cache:
            raise DataError(f"Symbol info not found: {symbol}")
        return self._cache[symbol]

    def get_all(self) -> dict[str, SymbolInfo]:
        """Get all cached symbol info."""
        if not self._is_cache_valid():
            self.refresh()
        return dict(self._cache)
