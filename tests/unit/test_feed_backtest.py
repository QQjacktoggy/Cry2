from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from bot.core.clock import SimClock
from bot.core.event_bus import EventBus
from bot.data.feed_backtest import BacktestFeed
from bot.data.storage import ParquetStorage
from bot.utils.time_utils import datetime_to_ms


def test_backtest_feed_supports_multiple_timeframes(tmp_path):
    storage = ParquetStorage(str(tmp_path))
    event_bus = EventBus()
    start_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    later_dt = datetime(2024, 1, 2, tzinfo=timezone.utc)

    for timeframe, ts, close in [
        ("4h", start_dt, 101.0),
        ("1d", later_dt, 110.0),
    ]:
        df = pd.DataFrame(
            [
                {
                    "timestamp": datetime_to_ms(ts),
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": close,
                    "volume": 1000.0,
                    "quote_volume": 1000.0,
                    "trade_count": 10,
                    "taker_buy_volume": 500.0,
                    "taker_buy_quote_volume": 500.0,
                }
            ]
        )
        storage.save_klines(df, "BTCUSDT", timeframe)

    feed = BacktestFeed(
        event_bus=event_bus,
        clock=SimClock(datetime_to_ms(start_dt)),
        storage=storage,
        symbols=["BTCUSDT"],
        timeframes=["4h", "1d"],
        start_ms=datetime_to_ms(start_dt),
        end_ms=datetime_to_ms(later_dt),
    )

    feed.start()
    events = []
    while feed.has_next():
        event = feed.next()
        if event is not None:
            events.append(event)

    assert [event.timeframe for event in events] == ["4h", "1d"]
