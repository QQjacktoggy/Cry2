#!/usr/bin/env python3
"""Download data from Binance API into backtest_tool data store.

Usage:
    python -m backtest_tool.scripts.download_data --symbol BTCUSDT --timeframe 4h --start 2023-01-01
    python -m backtest_tool.scripts.download_data --symbol ETHUSDT --timeframe 1h --start 2024-01-01 --end 2024-12-31
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import CatalogManager, DataImporter, DataStore


def main():
    parser = argparse.ArgumentParser(description="Download data from Binance API")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--timeframe", type=str, required=True, help="Timeframe (e.g., 1h, 4h, 1d)")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD), defaults to now")
    parser.add_argument("--data-type", choices=["klines", "funding"], default="klines",
                        help="Data type (default: klines)")
    args = parser.parse_args()

    store = DataStore()
    importer = DataImporter(store)
    catalog = CatalogManager(store)

    print(f"📡 Downloading {args.symbol} {args.timeframe} from {args.start} to {args.end or 'now'}...")

    try:
        if args.data_type == "klines":
            df = importer.download_klines(args.symbol, args.timeframe, args.start, args.end)
        else:
            df = importer.download_funding(args.symbol, args.start, args.end)

        print(f"✅ Downloaded {len(df)} rows")
        print(f"   Date range: {df.index[0]} → {df.index[-1]}")

    except Exception as e:
        print(f"❌ Download failed: {e}")
        sys.exit(1)

    print("\n📋 Updating data catalog...")
    catalog.update()
    print("✅ Done")


if __name__ == "__main__":
    main()
