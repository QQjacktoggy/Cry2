import pandas as pd
import numpy as np
import time
import os

def generate_data(symbol, timeframe, start_date, end_date):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    if timeframe == '1m':
        freq = '1min'
    elif timeframe == '5m':
        freq = '5min'
    elif timeframe == '15m':
        freq = '15min'
    elif timeframe == '1h':
        freq = '1h'
    elif timeframe == '4h':
        freq = '4h'
    elif timeframe == '8h':
        freq = '8h'
    elif timeframe == '1d':
        freq = '1d'
    else:
        freq = '1h'

    dates = pd.date_range(start, end, freq=freq)
    n = len(dates)

    # Generate random walk for prices but with HIGH volatility to trigger trades
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.05, n) # Huge variance to trip BB/Donchian
    prices = 50000 * np.cumprod(1 + returns)

    df = pd.DataFrame({
        'timestamp': dates.view('int64') // 10**6, # ms
        'open': prices,
        'high': prices * np.random.uniform(1.0, 1.05, n),
        'low': prices * np.random.uniform(0.95, 1.0, n),
        'close': prices * np.random.uniform(0.95, 1.05, n),
        'volume': np.random.uniform(10, 1000, n),
        'quote_volume': np.random.uniform(100000, 10000000, n),
        'trade_count': np.random.randint(100, 10000, n),
        'taker_buy_volume': np.random.uniform(5, 500, n),
        'taker_buy_quote_volume': np.random.uniform(50000, 5000000, n),
    })

    path = f"data/historical/klines/{symbol}/{timeframe}"
    os.makedirs(path, exist_ok=True)

    df.to_parquet(f"{path}/{start.year}.parquet")
    print(f"Generated mock data for {symbol} {timeframe}")

symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
timeframes = ['1m', '4h', '8h', '1d']

for sym in symbols:
    for tf in timeframes:
        generate_data(sym, tf, '2023-01-01', '2023-12-31')
