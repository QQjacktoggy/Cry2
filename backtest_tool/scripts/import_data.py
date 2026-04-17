#!/usr/bin/env python3
"""Import data from parent project into backtest_tool data store.

Usage:
    python -m backtest_tool.scripts.import_data --all
    python -m backtest_tool.scripts.import_data --symbol BTCUSDT --timeframe 4h
    python -m backtest_tool.scripts.import_data --funding
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import CatalogManager, DataImporter, DataStore


def main():
    parser = argparse.ArgumentParser(description="Import data from parent project")
    parser.add_argument("--symbol", type=str, nargs="+", help="Symbols (e.g., BTCUSDT ETHUSDT)")
    parser.add_argument("--timeframe", type=str, nargs="+", help="Timeframes (e.g., 1h 4h)")
    parser.add_argument("--all", action="store_true", help="Import all klines + funding")
    parser.add_argument("--funding", action="store_true", help="Also import funding rates")
    args = parser.parse_args()

    if not args.all and not args.symbol:
        parser.error("Either --all or --symbol is required")

    store = DataStore()
    importer = DataImporter(store)
    catalog = CatalogManager(store)

    parent_data_dir = str(PROJECT_ROOT / "data")

    if args.all:
        print("🔍 Importing ALL data from parent project...")
        summary = importer.import_from_parent(parent_data_dir)
        print(f"\n✅ Klines imported: {summary['klines_imported']}")
        print(f"✅ Funding imported: {summary['funding_imported']}")
        if summary["errors"]:
            print(f"⚠️  Errors: {len(summary['errors'])}")
            for e in summary["errors"]:
                print(f"   {e}")
    else:
        print(f"📥 Importing {args.symbol} {args.timeframe or 'all TFs'}...")
        summary = importer.import_from_parent(
            parent_data_dir,
            symbols=args.symbol,
            timeframes=args.timeframe,
        )
        print(f"✅ Klines imported: {summary['klines_imported']}")
        if args.funding:
            print(f"✅ Funding imported: {summary['funding_imported']}")

    print("\n📋 Updating data catalog...")
    catalog.update()
    print("✅ DATA_CATALOG.md updated")


if __name__ == "__main__":
    main()
