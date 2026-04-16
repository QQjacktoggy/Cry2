"""Funding rate data source and utilities."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import structlog

from bot.core.constants import FUNDING_INTERVAL_MS
from bot.data.fetcher import BinanceFetcher
from bot.data.storage import ParquetStorage

logger = structlog.get_logger(__name__)


class FundingRateManager:
    """Manages funding rate data fetching and storage."""

    def __init__(
        self,
        fetcher: BinanceFetcher,
        storage: ParquetStorage,
    ) -> None:
        self.fetcher = fetcher
        self.storage = storage

    def download_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Download and store funding rate history."""
        if end_ms is None:
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        all_data: list[pd.DataFrame] = []
        current_start = start_ms

        while current_start < end_ms:
            df = self.fetcher.fetch_funding_rate(
                symbol=symbol,
                start_time=current_start,
                end_time=end_ms,
                limit=1000,
            )
            if df.empty:
                break

            all_data.append(df)
            current_start = int(df["timestamp"].max()) + 1

        if not all_data:
            return pd.DataFrame()

        result = pd.concat(all_data, ignore_index=True)
        result = result.drop_duplicates(subset=["timestamp"], keep="last")
        result = result.sort_values("timestamp").reset_index(drop=True)

        self.storage.save_funding(result, symbol)
        logger.info("funding_history_downloaded", symbol=symbol, rows=len(result))
        return result

    def get_current_rate(self, symbol: str) -> float | None:
        """Get the latest funding rate for a symbol."""
        df = self.fetcher.fetch_funding_rate(symbol=symbol, limit=1)
        if df.empty:
            return None
        return float(df.iloc[-1]["funding_rate"])

    @staticmethod
    def annualize_rate(funding_rate: float) -> float:
        """Convert per-period funding rate to annualized rate.

        Funding is settled 3x daily (every 8 hours), so 1095 periods/year.
        """
        return funding_rate * 3 * 365 * 100  # as percentage
