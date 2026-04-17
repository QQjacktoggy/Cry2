#!/usr/bin/env python3
"""Import data from parent project into backtest_tool data store.

Usage:
    python -m backtest_tool.scripts.import_data --symbol BTCUSDT --timeframe 4h
    python -m backtest_tool.scripts.import_data --all
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import CatalogManager, DataImporter, DataStore


def main():
    parser = argparse.ArgumentParser(description="Import data from parent project")
    parser.add_argument("--symbol", type=str, help="Symbol to import (e.g., BTCUSDT)")
    parser.add_argument("--timeframe", type=str, help="Timeframe (e.g., 1h, 4h, 8h, 1d)")
    parser.add_argument("--all", action="store_true", help="Import all available data")
    parser.add_argument("--data-type", choices=["klines", "funding"], default="klines",
                        help="Data type to import (default: klines)")
    args = parser.parse_args()

    if not args.all and not (args.symbol and args.timeframe):
        parser.error("Either --all or both --symbol and --timeframe are required")

    store = DataStore()
    importer = DataImporter(store)
    catalog = CatalogManager(store)

    parent_data_dir = PROJECT_ROOT / "data" / "historical"

    if args.all:
        print("🔍 Scanning parent project data directory...")
        imported = _import_all(importer, parent_data_dir, args.data_type)
        print(f"\n✅ Imported {imported} datasets")
    else:
        print(f"📥 Importing {args.symbol} {args.timeframe} {args.data_type}...")
        try:
            df = importer.import_from_parent(args.symbol, args.timeframe, data_type=args.data_type)
            print(f"   ✅ {len(df)} rows imported")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            sys.exit(1)

    # Update catalog
    print("\n📋 Updating data catalog...")
    catalog.update()
    print("✅ DATA_CATALOG.md updated")


def _import_all(importer: DataImporter, parent_data_dir: Path, data_type: str) -> int:
    """Import all available data from parent project."""
    count = 0

    if data_type == "klines":
        klines_dir = parent_data_dir / "klines"
        if not klines_dir.exists():
            print(f"❌ Klines directory not found: {klines_dir}")
            return 0

        for symbol_dir in sorted(klines_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            symbol = symbol_dir.name
            for tf_dir in sorted(symbol_dir.iterdir()):
                if not tf_dir.is_dir():
                    continue
                timeframe = tf_dir.name
                try:
                    df = importer.import_from_parent(symbol, timeframe, data_type="klines")
                    print(f"   ✅ {symbol}/{timeframe}: {len(df)} rows")
                    count += 1
                except Exception as e:
                    print(f"   ⚠️ {symbol}/{timeframe}: {e}")

    elif data_type == "funding":
        funding_dir = parent_data_dir / "funding"
        if not funding_dir.exists():
            print(f"❌ Funding directory not found: {funding_dir}")
            return 0

        for parquet_file in sorted(funding_dir.glob("*.parquet")):
            symbol = parquet_file.stem
            try:
                df = importer.import_from_parent(symbol, "8h", data_type="funding")
                print(f"   ✅ {symbol}/funding: {len(df)} rows")
                count += 1
            except Exception as e:
                print(f"   ⚠️ {symbol}/funding: {e}")

    return count


if __name__ == "__main__":
    main()
