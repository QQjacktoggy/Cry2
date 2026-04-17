#!/usr/bin/env python3
"""Download data from Binance API into backtest_tool data store.

Usage:
    python -m backtest_tool.scripts.download_data --symbol BTCUSDT --timeframe 4h --start 2023-01-01
    python -m backtest_tool.scripts.download_data --symbol BTCUSDT ETHUSDT --timeframe 4h 1h --start 2024-01-01 --funding
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import CatalogManager, DataImporter, DataStore


def main():
    parser = argparse.ArgumentParser(description="Download data from Binance API")
    parser.add_argument("--symbol", type=str, nargs="+", required=True, help="Symbols (e.g., BTCUSDT ETHUSDT)")
    parser.add_argument("--timeframe", type=str, nargs="+", required=True, help="Timeframes (e.g., 1h 4h)")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD), defaults to now")
    parser.add_argument("--funding", action="store_true", help="Also download funding rates")
    args = parser.parse_args()

    store = DataStore()
    importer = DataImporter(store)
    catalog = CatalogManager(store)

    print(f"📡 Downloading {args.symbol} × {args.timeframe} from {args.start} to {args.end or 'now'}...")
    if args.funding:
        print("   Including funding rates")

    try:
        summary = importer.download_from_binance(
            symbols=args.symbol,
            timeframes=args.timeframe,
            start=args.start,
            end=args.end,
            include_funding=args.funding,
        )

        print(f"\n✅ Klines downloaded: {summary['klines_downloaded']}")
        if args.funding:
            print(f"✅ Funding downloaded: {summary['funding_downloaded']}")
        if summary["errors"]:
            print(f"⚠️  Errors: {len(summary['errors'])}")
            for e in summary["errors"]:
                print(f"   {e}")

    except Exception as e:
        print(f"❌ Download failed: {e}")
        sys.exit(1)

    print("\n📋 Updating data catalog...")
    catalog.update()
    print("✅ Done")


if __name__ == "__main__":
    main()
