"""Parquet read/write with incremental updates and deduplication."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from bot.core.exceptions import DataError
from bot.data.schemas import KLINE_COLUMNS, KLINE_DTYPES

logger = structlog.get_logger(__name__)


class ParquetStorage:
    """Handles Parquet file read/write operations."""

    def __init__(self, data_dir: str = "./data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _kline_path(self, symbol: str, timeframe: str, year: int) -> Path:
        """Get path for a kline parquet file."""
        path = self.data_dir / "historical" / "klines" / symbol / timeframe
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{year}.parquet"

    def _funding_path(self, symbol: str) -> Path:
        """Get path for a funding rate parquet file."""
        path = self.data_dir / "historical" / "funding"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{symbol}.parquet"

    def save_klines(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        """Save K-line data, splitting by year with deduplication."""
        if df.empty:
            return

        df = df[KLINE_COLUMNS].copy()
        for col, dtype in KLINE_DTYPES.items():
            df[col] = df[col].astype(dtype)

        # Split by year
        df["year"] = pd.to_datetime(df["timestamp"], unit="ms").dt.year
        for year, group in df.groupby("year"):
            file_path = self._kline_path(symbol, timeframe, int(year))
            group = group.drop(columns=["year"])

            if file_path.exists():
                existing = pd.read_parquet(file_path)
                combined = pd.concat([existing, group], ignore_index=True)
                combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                combined = combined.sort_values("timestamp").reset_index(drop=True)
            else:
                combined = group.sort_values("timestamp").reset_index(drop=True)

            combined.to_parquet(file_path, engine="pyarrow", index=False)
            logger.debug(
                "klines_saved",
                symbol=symbol,
                timeframe=timeframe,
                year=year,
                rows=len(combined),
            )

    def load_klines(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Load K-line data from Parquet files."""
        kline_dir = self.data_dir / "historical" / "klines" / symbol / timeframe
        if not kline_dir.exists():
            logger.warning("kline_dir_not_found", path=str(kline_dir))
            return pd.DataFrame(columns=KLINE_COLUMNS)

        files = sorted(kline_dir.glob("*.parquet"))
        if not files:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        dfs = []
        for f in files:
            try:
                df = pd.read_parquet(f)
                dfs.append(df)
            except Exception as e:
                logger.error("parquet_read_error", file=str(f), error=str(e))

        if not dfs:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        result = pd.concat(dfs, ignore_index=True)
        result = result.sort_values("timestamp").reset_index(drop=True)

        if start_ms is not None:
            result = result[result["timestamp"] >= start_ms]
        if end_ms is not None:
            result = result[result["timestamp"] <= end_ms]

        return result.reset_index(drop=True)

    def save_funding(self, df: pd.DataFrame, symbol: str) -> None:
        """Save funding rate data with deduplication."""
        if df.empty:
            return

        file_path = self._funding_path(symbol)

        if file_path.exists():
            existing = pd.read_parquet(file_path)
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
            combined = combined.sort_values("timestamp").reset_index(drop=True)
        else:
            combined = df.sort_values("timestamp").reset_index(drop=True)

        combined.to_parquet(file_path, engine="pyarrow", index=False)
        logger.debug("funding_saved", symbol=symbol, rows=len(combined))

    def load_funding(
        self,
        symbol: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Load funding rate data from Parquet."""
        file_path = self._funding_path(symbol)
        if not file_path.exists():
            return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate"])

        df = pd.read_parquet(file_path)

        if start_ms is not None:
            df = df[df["timestamp"] >= start_ms]
        if end_ms is not None:
            df = df[df["timestamp"] <= end_ms]

        return df.reset_index(drop=True)

    def get_latest_timestamp(self, symbol: str, timeframe: str) -> int | None:
        """Get the latest timestamp in stored kline data."""
        df = self.load_klines(symbol, timeframe)
        if df.empty:
            return None
        return int(df["timestamp"].max())
