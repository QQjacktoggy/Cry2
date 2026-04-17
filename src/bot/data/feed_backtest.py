"""Backtest data feed - reads from Parquet files and produces events in time order."""

from __future__ import annotations

from datetime import timezone
from typing import Any

import pandas as pd
import structlog

from bot.core.clock import SimClock
from bot.core.constants import FUNDING_INTERVAL_MS
from bot.core.event_bus import EventBus
from bot.core.events import FundingEvent, MarketEvent
from bot.data.feed_base import DataFeed
from bot.data.storage import ParquetStorage
from bot.utils.time_utils import ms_to_datetime

logger = structlog.get_logger(__name__)


class BacktestFeed(DataFeed):
    """Feeds historical data from Parquet files as events.

    Merges multiple symbols and timeframes into a single time-ordered stream.
    Signals are generated at bar close, execution at next bar open (no lookahead).
    """

    def __init__(
        self,
        event_bus: EventBus,
        clock: SimClock,
        storage: ParquetStorage,
        symbols: list[str],
        timeframe: str,
        start_ms: int,
        end_ms: int,
        funding_data: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        super().__init__(event_bus, clock)
        self.storage = storage
        self.symbols = symbols
        self.timeframe = timeframe
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.funding_data = funding_data or {}

        self._events: list[dict[str, Any]] = []
        self._index = 0

    def _build_event_queue(self) -> None:
        """Load all data and build time-ordered event queue."""
        all_events: list[dict[str, Any]] = []

        # Load kline data for each symbol
        for symbol in self.symbols:
            df = self.storage.load_klines(symbol, self.timeframe, self.start_ms, self.end_ms)
            if df.empty:
                logger.warning("no_kline_data", symbol=symbol, timeframe=self.timeframe)
                continue

            for _, row in df.iterrows():
                all_events.append({
                    "type": "market",
                    "timestamp": int(row["timestamp"]),
                    "symbol": symbol,
                    "data": row.to_dict(),
                })

        # Load funding rate data
        for symbol in self.symbols:
            if symbol in self.funding_data:
                fdf = self.funding_data[symbol]
            else:
                fdf = self.storage.load_funding(symbol, self.start_ms, self.end_ms)

            if fdf.empty:
                continue

            for _, row in fdf.iterrows():
                ts = int(row["timestamp"])
                if self.start_ms <= ts <= self.end_ms:
                    all_events.append({
                        "type": "funding",
                        "timestamp": ts,
                        "symbol": symbol,
                        "data": {"funding_rate": float(row["funding_rate"])},
                    })

        # Sort by timestamp
        all_events.sort(key=lambda e: e["timestamp"])
        self._events = all_events

        logger.info(
            "backtest_feed_built",
            total_events=len(all_events),
            symbols=self.symbols,
            timeframe=self.timeframe,
        )

    def start(self) -> None:
        """Build event queue and start feed."""
        self._build_event_queue()
        self._index = 0
        self._running = True
        logger.info("backtest_feed_started")

    def stop(self) -> None:
        """Stop the feed."""
        self._running = False
        logger.info("backtest_feed_stopped")

    def has_next(self) -> bool:
        """Check if more events are available."""
        return self._index < len(self._events)

    def next(self) -> MarketEvent | FundingEvent | None:
        """Get the next event in time order."""
        if not self.has_next():
            return None

        event_data = self._events[self._index]
        self._index += 1

        # Advance simulated clock
        assert isinstance(self.clock, SimClock)
        self.clock.set_time(event_data["timestamp"])

        ts_dt = ms_to_datetime(event_data["timestamp"])

        if event_data["type"] == "market":
            data = event_data["data"]
            return MarketEvent(
                timestamp=ts_dt,
                symbol=event_data["symbol"],
                timeframe=self.timeframe,
                open=float(data["open"]),
                high=float(data["high"]),
                low=float(data["low"]),
                close=float(data["close"]),
                volume=float(data["volume"]),
                quote_volume=float(data.get("quote_volume", 0)),
                trade_count=int(data.get("trade_count", 0)),
                taker_buy_volume=float(data.get("taker_buy_volume", 0)),
                taker_buy_quote_volume=float(data.get("taker_buy_quote_volume", 0)),
                bar_timestamp=ts_dt,
                source="backtest",
            )

        if event_data["type"] == "funding":
            return FundingEvent(
                timestamp=ts_dt,
                symbol=event_data["symbol"],
                funding_rate=event_data["data"]["funding_rate"],
                source="backtest",
            )

        return None

    @property
    def total_events(self) -> int:
        """Total number of events in the queue."""
        return len(self._events)

    @property
    def progress(self) -> float:
        """Current progress (0.0 to 1.0)."""
        if not self._events:
            return 1.0
        return self._index / len(self._events)
