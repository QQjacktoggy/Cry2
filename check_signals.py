"""Quick check: what signals does each strategy generate on latest bar?"""
import sys
sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app")

import pandas as pd
from backtest_tool.strategies import STRATEGY_MAP

# Strategy names we care about (from bot config)
vbt_strategies = [
    "trend_donchian_mtf",
    "trend_donchian_adx_slope",
    "momentum_ranking",
    "grid_trend_bias",
    "tail_risk_hedge",
    "breakout_squeeze",
]

symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

import os
from bot.exchange.binance_rest import BinanceRestClient
key = os.environ["BINANCE_API_KEY"]
secret = os.environ["BINANCE_API_SECRET"]
c = BinanceRestClient(api_key=key, api_secret=secret, mode="testnet")

for sym in symbols:
    klines = c.get_klines(symbol=sym, interval="4h", limit=210)
    if not klines:
        continue
    df = pd.DataFrame(klines, columns=[
        "ts","open","high","low","close","vol","close_ts","qvol","n","taker_buy_vol","taker_buy_qvol","ignore"
    ])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["vol"] = df["vol"].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    df = df.set_index("ts")
    df = df[:-1]  # exclude current open bar

    for strat_name in vbt_strategies:
        if strat_name not in STRATEGY_MAP:
            continue
        cls = STRATEGY_MAP[strat_name]
        try:
            s = cls()
            entries = s.generate_entries(df)
            last_entry = bool(entries.iloc[-1]) if len(entries) else False
            if last_entry:
                print(f"SIGNAL  {sym:12s}  {strat_name:30s}  LONG ENTRY")
        except Exception as e:
            print(f"ERROR   {sym:12s}  {strat_name:30s}  {e}")
