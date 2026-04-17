import pandas as pd
import numpy as np
import time
import os

def generate_funding(symbol, start_date, end_date):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    dates = pd.date_range(start, end, freq='8h')

    n = len(dates)

    # Generate funding rates
    df = pd.DataFrame({
        'timestamp': dates.view('int64') // 10**6, # ms
        'symbol': symbol,
        'funding_rate': np.random.normal(0.0001, 0.0005, n),
    })

    path = f"data/historical/funding"
    os.makedirs(path, exist_ok=True)

    df.to_parquet(f"{path}/{symbol}.parquet")
    print(f"Generated mock funding data for {symbol}")

symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

for sym in symbols:
    generate_funding(sym, '2023-01-01', '2023-12-31')
