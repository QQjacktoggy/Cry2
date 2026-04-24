import os
from bot.exchange.binance_rest import BinanceRestClient
key = os.environ["BINANCE_API_KEY"]
secret = os.environ["BINANCE_API_SECRET"]
c = BinanceRestClient(api_key=key, api_secret=secret, mode="testnet")
print("Balance:", c.get_balance())
for sym in ["SOLUSDT","BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT"]:
    p = c.get_position(sym)
    if p.quantity != 0:
        print(f"  {sym}: qty={p.quantity} entry={p.entry_price} pnl={p.unrealized_pnl}")
