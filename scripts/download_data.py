#!/usr/bin/env python3
"""Download historical K-line and funding rate data from Binance."""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.config.loader import load_config
from bot.config.env import load_env
from bot.core.logger import setup_logging
from bot.data.fetcher import BinanceFetcher
from bot.data.storage import ParquetStorage
from bot.data.funding_rate import FundingRateManager
from bot.utils.time_utils import parse_date, datetime_to_ms

import structlog

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical data")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--timeframes", nargs="+", default=["1m", "4h", "1d"])
    parser.add_argument("--start", default="2024-01-01", help="Start date")
    parser.add_argument("--end", default=None, help="End date (default: now)")
    parser.add_argument("--funding", action="store_true", help="Also download funding rates")
    parser.add_argument("--data-dir", default="./data", help="Data directory")
    args = parser.parse_args()

    load_env()
    setup_logging("INFO", json_format=False)

    fetcher = BinanceFetcher()
    storage = ParquetStorage(args.data_dir)

    start_ms = datetime_to_ms(parse_date(args.start))
    end_ms = datetime_to_ms(parse_date(args.end)) if args.end else None

    for symbol in args.symbols:
        for tf in args.timeframes:
            print(f"Downloading {symbol} {tf} from {args.start}...")
            df = fetcher.fetch_all_klines(symbol, tf, start_ms, end_ms)
            if not df.empty:
                storage.save_klines(df, symbol, tf)
                print(f"  Saved {len(df)} candles")
            else:
                print(f"  No data returned")

        if args.funding:
            print(f"Downloading {symbol} funding rates...")
            fm = FundingRateManager(fetcher, storage)
            df = fm.download_history(symbol, start_ms, end_ms)
            print(f"  Saved {len(df)} funding rate records")

    print("Download complete!")


if __name__ == "__main__":
    main()
