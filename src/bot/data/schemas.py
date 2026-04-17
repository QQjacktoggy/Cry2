"""Data schema definitions for K-lines and funding rates."""

from __future__ import annotations

KLINE_COLUMNS = [
    "timestamp",       # int64 (ms)
    "open",            # float64
    "high",            # float64
    "low",             # float64
    "close",           # float64
    "volume",          # float64
    "quote_volume",    # float64
    "trade_count",     # int64
    "taker_buy_volume",        # float64
    "taker_buy_quote_volume",  # float64
]

KLINE_DTYPES = {
    "timestamp": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "quote_volume": "float64",
    "trade_count": "int64",
    "taker_buy_volume": "float64",
    "taker_buy_quote_volume": "float64",
}

FUNDING_COLUMNS = [
    "timestamp",       # int64 (ms)
    "symbol",          # str
    "funding_rate",    # float64
]

FUNDING_DTYPES = {
    "timestamp": "int64",
    "symbol": "object",
    "funding_rate": "float64",
}
