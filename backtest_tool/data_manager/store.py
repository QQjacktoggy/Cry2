"""DataStore: Parquet-based storage for klines and funding rates.

Reads/writes Parquet files organized by symbol/timeframe/year.
Compatible with the parent project's data format.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger(__name__)

KLINE_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
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

FUNDING_COLUMNS = ["timestamp", "symbol", "funding_rate"]
FUNDING_DTYPES = {"timestamp": "int64", "symbol": "object", "funding_rate": "float64"}


class DataStore:
    """Parquet-based data store for klines and funding rates."""

    _DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

    def __init__(self, data_dir: str | None = None) -> None:
        """Initialize data store and create directory structure.

        Args:
            data_dir: Root directory for data storage. Defaults to backtest_tool/data/.
        """
        self.data_dir = Path(data_dir) if data_dir else self._DEFAULT_DATA_DIR
        self.klines_dir = self.data_dir / "klines"
        self.funding_dir = self.data_dir / "funding"
        self.klines_dir.mkdir(parents=True, exist_ok=True)
        self.funding_dir.mkdir(parents=True, exist_ok=True)

    def _kline_path(self, symbol: str, timeframe: str, year: int) -> Path:
        """Get path for a kline parquet file."""
        path = self.klines_dir / symbol / timeframe
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{year}.parquet"

    def _funding_path(self, symbol: str) -> Path:
        """Get path for a funding rate parquet file."""
        self.funding_dir.mkdir(parents=True, exist_ok=True)
        return self.funding_dir / f"{symbol}.parquet"

    def save_klines(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        """Save kline data, splitting by year with deduplication.

        Args:
            df: DataFrame with KLINE_COLUMNS.
            symbol: Trading pair symbol (e.g., 'BTCUSDT').
            timeframe: Candle timeframe (e.g., '4h', '1m').
        """
        if df.empty:
            logger.warning("Empty DataFrame, skipping save", symbol=symbol, timeframe=timeframe)
            return

        df = df.copy()
        # Ensure required columns exist
        for col in KLINE_COLUMNS:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        df = df[KLINE_COLUMNS].copy()
        for col, dtype in KLINE_DTYPES.items():
            df[col] = df[col].astype(dtype)

        # Split by year
        df["_year"] = pd.to_datetime(df["timestamp"], unit="ms").dt.year
        for year, group in df.groupby("_year"):
            group = group.drop(columns=["_year"])
            file_path = self._kline_path(symbol, timeframe, int(year))

            if file_path.exists():
                existing = pd.read_parquet(file_path)
                combined = pd.concat([existing, group], ignore_index=True)
                combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                combined = combined.sort_values("timestamp").reset_index(drop=True)
            else:
                combined = group.sort_values("timestamp").reset_index(drop=True)

            combined.to_parquet(file_path, index=False, engine="pyarrow")
            logger.info(
                "Saved klines",
                symbol=symbol,
                timeframe=timeframe,
                year=int(year),
                rows=len(combined),
            )

    def load_klines(
        self,
        symbol: str,
        timeframe: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load kline data with optional date filtering.

        Args:
            symbol: Trading pair symbol.
            timeframe: Candle timeframe.
            start: Start date string 'YYYY-MM-DD' (inclusive).
            end: End date string 'YYYY-MM-DD' (inclusive).

        Returns:
            DataFrame with DatetimeIndex and OHLCV columns.
        """
        kline_dir = self.klines_dir / symbol / timeframe
        if not kline_dir.exists():
            logger.warning("No data found", symbol=symbol, timeframe=timeframe)
            return pd.DataFrame(columns=KLINE_COLUMNS)

        parquet_files = sorted(kline_dir.glob("*.parquet"))
        if not parquet_files:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        dfs = []
        for f in parquet_files:
            try:
                dfs.append(pd.read_parquet(f))
            except Exception as e:
                logger.error("Failed to read parquet", path=str(f), error=str(e))

        if not dfs:
            return pd.DataFrame(columns=KLINE_COLUMNS)

        df = pd.concat(dfs, ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        # Apply date filters
        if start is not None:
            start_ms = int(pd.Timestamp(start).timestamp() * 1000)
            df = df[df["timestamp"] >= start_ms]
        if end is not None:
            end_ms = int((pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)).timestamp() * 1000)
            df = df[df["timestamp"] <= end_ms]

        # Set DatetimeIndex
        df.index = pd.to_datetime(df["timestamp"], unit="ms")
        df.index.name = "datetime"
        return df

    def save_funding(self, df: pd.DataFrame, symbol: str) -> None:
        """Save funding rate data with deduplication.

        Args:
            df: DataFrame with funding columns (timestamp, funding_rate).
            symbol: Trading pair symbol.
        """
        if df.empty:
            return

        df = df.copy()
        if "symbol" not in df.columns:
            df["symbol"] = symbol

        for col in FUNDING_COLUMNS:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        df = df[FUNDING_COLUMNS].copy()
        for col, dtype in FUNDING_DTYPES.items():
            df[col] = df[col].astype(dtype)

        file_path = self._funding_path(symbol)
        if file_path.exists():
            existing = pd.read_parquet(file_path)
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
            combined = combined.sort_values("timestamp").reset_index(drop=True)
        else:
            combined = df.sort_values("timestamp").reset_index(drop=True)

        combined.to_parquet(file_path, index=False, engine="pyarrow")
        logger.info("Saved funding rates", symbol=symbol, rows=len(combined))

    def load_funding(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load funding rate data with optional date filtering.

        Args:
            symbol: Trading pair symbol.
            start: Start date string 'YYYY-MM-DD' (inclusive).
            end: End date string 'YYYY-MM-DD' (inclusive).

        Returns:
            DataFrame with DatetimeIndex and funding_rate column.
        """
        file_path = self._funding_path(symbol)
        if not file_path.exists():
            logger.warning("No funding data found", symbol=symbol)
            return pd.DataFrame(columns=FUNDING_COLUMNS)

        df = pd.read_parquet(file_path)

        if start is not None:
            start_ms = int(pd.Timestamp(start).timestamp() * 1000)
            df = df[df["timestamp"] >= start_ms]
        if end is not None:
            end_ms = int((pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)).timestamp() * 1000)
            df = df[df["timestamp"] <= end_ms]

        df.index = pd.to_datetime(df["timestamp"], unit="ms")
        df.index.name = "datetime"
        return df

    def list_available(self) -> dict:
        """Return summary of all available datasets.

        Returns:
            Dict with 'klines' and 'funding' lists of dataset info.
        """
        result: dict[str, list[dict]] = {"klines": [], "funding": []}

        # Scan klines
        if self.klines_dir.exists():
            for symbol_dir in sorted(self.klines_dir.iterdir()):
                if not symbol_dir.is_dir():
                    continue
                for tf_dir in sorted(symbol_dir.iterdir()):
                    if not tf_dir.is_dir():
                        continue
                    parquet_files = list(tf_dir.glob("*.parquet"))
                    if not parquet_files:
                        continue

                    total_rows = 0
                    total_size = 0
                    min_ts = float("inf")
                    max_ts = float("-inf")

                    for f in parquet_files:
                        try:
                            pf = pq.read_metadata(f)
                            total_rows += pf.num_rows
                            total_size += f.stat().st_size
                            tbl = pq.read_table(f, columns=["timestamp"])
                            ts_col = tbl.column("timestamp")
                            if len(ts_col) > 0:
                                min_ts = min(min_ts, ts_col[0].as_py())
                                max_ts = max(max_ts, ts_col[-1].as_py())
                        except Exception:
                            continue

                    if total_rows > 0:
                        result["klines"].append({
                            "symbol": symbol_dir.name,
                            "timeframe": tf_dir.name,
                            "start": pd.Timestamp(min_ts, unit="ms").strftime("%Y-%m-%d"),
                            "end": pd.Timestamp(max_ts, unit="ms").strftime("%Y-%m-%d"),
                            "rows": total_rows,
                            "size_mb": round(total_size / (1024 * 1024), 2),
                        })

        # Scan funding
        if self.funding_dir.exists():
            for f in sorted(self.funding_dir.glob("*.parquet")):
                try:
                    df = pd.read_parquet(f, columns=["timestamp"])
                    if len(df) > 0:
                        result["funding"].append({
                            "symbol": f.stem,
                            "start": pd.Timestamp(df["timestamp"].min(), unit="ms").strftime("%Y-%m-%d"),
                            "end": pd.Timestamp(df["timestamp"].max(), unit="ms").strftime("%Y-%m-%d"),
                            "rows": len(df),
                            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                        })
                except Exception:
                    continue

        return result
