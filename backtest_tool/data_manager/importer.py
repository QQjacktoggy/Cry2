"""DataImporter: Import data from parent project or Binance API.

Handles importing Parquet files from the parent project's data directory
and downloading historical data directly from Binance.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import structlog
from tqdm import tqdm

from backtest_tool.data_manager.store import DataStore

logger = structlog.get_logger(__name__)


class DataImporter:
    """Imports data from parent project or Binance API."""

    def __init__(self, store: DataStore) -> None:
        """Initialize importer with a DataStore.

        Args:
            store: DataStore instance for saving imported data.
        """
        self.store = store

    def import_from_parent(
        self,
        source_dir: str,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
    ) -> dict:
        """Import data from parent project's data/historical/ directory.

        Args:
            source_dir: Path to parent project's data directory
                        (e.g., '../data' or '../data/historical').
            symbols: Optional list of symbols to import. None = all.
            timeframes: Optional list of timeframes to import. None = all.

        Returns:
            Summary dict with counts of imported datasets.
        """
        source = Path(source_dir)

        # Auto-detect: if 'historical' subdir exists, use it
        if (source / "historical").exists():
            source = source / "historical"

        summary = {"klines_imported": 0, "funding_imported": 0, "errors": []}

        # Import klines
        klines_dir = source / "klines"
        if klines_dir.exists():
            symbol_dirs = sorted(klines_dir.iterdir())
            for symbol_dir in tqdm(symbol_dirs, desc="Importing klines"):
                if not symbol_dir.is_dir():
                    continue
                symbol = symbol_dir.name
                if symbols and symbol not in symbols:
                    continue

                for tf_dir in sorted(symbol_dir.iterdir()):
                    if not tf_dir.is_dir():
                        continue
                    tf = tf_dir.name
                    if timeframes and tf not in timeframes:
                        continue

                    parquet_files = sorted(tf_dir.glob("*.parquet"))
                    for pf in parquet_files:
                        try:
                            df = pd.read_parquet(pf)
                            self.store.save_klines(df, symbol, tf)
                            summary["klines_imported"] += 1
                        except Exception as e:
                            err = f"Failed to import {pf}: {e}"
                            logger.error(err)
                            summary["errors"].append(err)

        # Import funding rates
        funding_dir = source / "funding"
        if funding_dir.exists():
            for pf in tqdm(sorted(funding_dir.glob("*.parquet")), desc="Importing funding"):
                symbol = pf.stem
                if symbols and symbol not in symbols:
                    continue
                try:
                    df = pd.read_parquet(pf)
                    self.store.save_funding(df, symbol)
                    summary["funding_imported"] += 1
                except Exception as e:
                    err = f"Failed to import {pf}: {e}"
                    logger.error(err)
                    summary["errors"].append(err)

        logger.info(
            "Import complete",
            klines=summary["klines_imported"],
            funding=summary["funding_imported"],
            errors=len(summary["errors"]),
        )
        return summary

    def download_from_binance(
        self,
        symbols: list[str],
        timeframes: list[str],
        start: str,
        end: str | None = None,
        include_funding: bool = False,
    ) -> dict:
        """Download historical data from Binance API.

        Args:
            symbols: List of trading pair symbols.
            timeframes: List of kline timeframes.
            start: Start date string 'YYYY-MM-DD'.
            end: End date string 'YYYY-MM-DD'. None = now.
            include_funding: Whether to also download funding rates.

        Returns:
            Summary dict with counts of downloaded datasets.
        """
        from binance.client import Client

        client = Client()  # Public endpoints, no API key needed for klines
        summary = {"klines_downloaded": 0, "funding_downloaded": 0, "errors": []}

        # Map timeframe strings to Binance interval constants
        tf_map = {
            "1m": Client.KLINE_INTERVAL_1MINUTE,
            "3m": Client.KLINE_INTERVAL_3MINUTE,
            "5m": Client.KLINE_INTERVAL_5MINUTE,
            "15m": Client.KLINE_INTERVAL_15MINUTE,
            "30m": Client.KLINE_INTERVAL_30MINUTE,
            "1h": Client.KLINE_INTERVAL_1HOUR,
            "2h": Client.KLINE_INTERVAL_2HOUR,
            "4h": Client.KLINE_INTERVAL_4HOUR,
            "6h": Client.KLINE_INTERVAL_6HOUR,
            "8h": Client.KLINE_INTERVAL_8HOUR,
            "12h": Client.KLINE_INTERVAL_12HOUR,
            "1d": Client.KLINE_INTERVAL_1DAY,
        }

        total_tasks = len(symbols) * len(timeframes)
        with tqdm(total=total_tasks, desc="Downloading klines") as pbar:
            for symbol in symbols:
                for tf in timeframes:
                    pbar.set_postfix_str(f"{symbol}/{tf}")
                    try:
                        interval = tf_map.get(tf)
                        if interval is None:
                            summary["errors"].append(f"Unsupported timeframe: {tf}")
                            pbar.update(1)
                            continue

                        # Check for existing data (incremental download)
                        existing = self.store.load_klines(symbol, tf)
                        actual_start = start
                        if not existing.empty:
                            last_ts = existing["timestamp"].max()
                            actual_start = pd.Timestamp(last_ts, unit="ms").strftime("%Y-%m-%d")
                            logger.info(
                                "Incremental download",
                                symbol=symbol,
                                timeframe=tf,
                                from_date=actual_start,
                            )

                        klines = client.get_historical_klines(
                            symbol=symbol,
                            interval=interval,
                            start_str=actual_start,
                            end_str=end,
                        )

                        if klines:
                            df = self._binance_klines_to_df(klines)
                            self.store.save_klines(df, symbol, tf)
                            summary["klines_downloaded"] += 1
                            logger.info(
                                "Downloaded klines",
                                symbol=symbol,
                                timeframe=tf,
                                rows=len(df),
                            )

                    except Exception as e:
                        err = f"Failed to download {symbol}/{tf}: {e}"
                        logger.error(err)
                        summary["errors"].append(err)
                    pbar.update(1)

        # Download funding rates
        if include_funding:
            for symbol in tqdm(symbols, desc="Downloading funding rates"):
                try:
                    funding_data = self._download_funding_rates(client, symbol, start, end)
                    if not funding_data.empty:
                        self.store.save_funding(funding_data, symbol)
                        summary["funding_downloaded"] += 1
                except Exception as e:
                    err = f"Failed to download funding for {symbol}: {e}"
                    logger.error(err)
                    summary["errors"].append(err)

        logger.info(
            "Download complete",
            klines=summary["klines_downloaded"],
            funding=summary["funding_downloaded"],
            errors=len(summary["errors"]),
        )
        return summary

    def _binance_klines_to_df(self, klines: list) -> pd.DataFrame:
        """Convert Binance API kline response to DataFrame.

        Args:
            klines: Raw kline data from Binance API.

        Returns:
            DataFrame with standardized KLINE_COLUMNS.
        """
        from backtest_tool.data_manager.store import KLINE_COLUMNS

        rows = []
        for k in klines:
            rows.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
                "trade_count": int(k[8]),
                "taker_buy_volume": float(k[9]),
                "taker_buy_quote_volume": float(k[10]),
            })

        return pd.DataFrame(rows, columns=KLINE_COLUMNS)

    def _download_funding_rates(
        self,
        client: "Client",
        symbol: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Download funding rate history from Binance.

        Args:
            client: Binance client instance.
            symbol: Trading pair symbol.
            start: Start date string.
            end: End date string.

        Returns:
            DataFrame with funding rate data.
        """
        from backtest_tool.data_manager.store import FUNDING_COLUMNS

        start_ms = int(pd.Timestamp(start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(end).timestamp() * 1000) if end else None

        all_rates: list[dict] = []
        current_start = start_ms
        limit = 1000

        while True:
            params = {"symbol": symbol, "startTime": current_start, "limit": limit}
            if end_ms:
                params["endTime"] = end_ms

            try:
                rates = client.futures_funding_rate(**params)
            except Exception:
                break

            if not rates:
                break

            for r in rates:
                all_rates.append({
                    "timestamp": int(r["fundingTime"]),
                    "symbol": symbol,
                    "funding_rate": float(r["fundingRate"]),
                })

            if len(rates) < limit:
                break

            current_start = int(rates[-1]["fundingTime"]) + 1

        if not all_rates:
            return pd.DataFrame(columns=FUNDING_COLUMNS)

        return pd.DataFrame(all_rates)
