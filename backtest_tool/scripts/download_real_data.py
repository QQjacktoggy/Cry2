#!/usr/bin/env python3
"""
在本機執行此腳本，下載真實 BTC/USDT K 線數據並儲存為 Parquet。
完成後將 backtest_tool/data/ 整個資料夾複製回沙盒的相同路徑即可。

用法（本機執行）：
    pip install requests pandas pyarrow
    python backtest_tool/scripts/download_real_data.py

下載來源：Binance 公開 REST API（不需要 API Key）
"""

from __future__ import annotations

import time
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd

# ── 設定 ──────────────────────────────────────────────
SYMBOL     = "BTCUSDT"
START_DATE = "2024-04-17"
END_DATE   = "2026-04-17"
TIMEFRAMES = ["4h", "1h", "1d"]          # 需要的時框
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "klines"

BINANCE_URL = "https://api.binance.com/api/v3/klines"
INTERVAL_MAP = {"4h": "4h", "1h": "1h", "1d": "1d"}
LIMIT = 1000   # Binance 每次最多 1000 根


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    """從 Binance 公開 API 分批下載 K 線（無需 API Key）。"""
    all_rows: list[list] = []
    cur = start_ms

    while cur < end_ms:
        params = {
            "symbol":    symbol,
            "interval":  interval,
            "startTime": cur,
            "endTime":   end_ms,
            "limit":     LIMIT,
        }
        resp = requests.get(BINANCE_URL, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        # 最後一根的 close time + 1ms 作為下一批起點
        cur = rows[-1][6] + 1
        print(f"  已下載 {len(all_rows):>6} 根  ({pd.Timestamp(rows[-1][0], unit='ms').date()})")
        time.sleep(0.15)   # 避免限速

    return all_rows


def rows_to_df(rows: list[list]) -> pd.DataFrame:
    """將 Binance K 線原始列表轉成 DataStore 需要的格式。"""
    cols = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trade_count",
        "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df = df[[
        "timestamp", "open", "high", "low", "close", "volume",
        "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
    ]]
    numeric_cols = ["open", "high", "low", "close", "volume",
                    "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df["timestamp"]   = df["timestamp"].astype("int64")
    df["trade_count"] = df["trade_count"].astype("int64")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def save_parquet(df: pd.DataFrame, symbol: str, timeframe: str) -> None:
    """按年份分檔儲存 Parquet。"""
    df["_year"] = pd.to_datetime(df["timestamp"], unit="ms").dt.year
    for year, group in df.groupby("_year"):
        out_dir = OUTPUT_DIR / symbol / timeframe
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{year}.parquet"
        group = group.drop(columns=["_year"]).reset_index(drop=True)
        group.to_parquet(path, index=False, engine="pyarrow")
        print(f"  ✅ 儲存 {path}  ({len(group)} 筆)")


def main() -> None:
    start_ms = int(pd.Timestamp(START_DATE, tz="UTC").timestamp() * 1000)
    end_ms   = int(pd.Timestamp(END_DATE,   tz="UTC").timestamp() * 1000)

    for tf in TIMEFRAMES:
        interval = INTERVAL_MAP[tf]
        print(f"\n{'='*60}")
        print(f"下載 {SYMBOL} {tf}  {START_DATE} → {END_DATE}")
        print(f"{'='*60}")
        rows = fetch_klines(SYMBOL, interval, start_ms, end_ms)
        if not rows:
            print("  ⚠️  無資料，跳過")
            continue
        df = rows_to_df(rows)
        print(f"  總計 {len(df)} 根 K 線")
        save_parquet(df, SYMBOL, tf)

    print(f"\n✅ 完成！資料存於：{OUTPUT_DIR.parent}")
    print("請將 backtest_tool/data/ 整個資料夾複製回沙盒的相同路徑。")


if __name__ == "__main__":
    main()
