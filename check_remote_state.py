import os
import sqlite3

from bot.config.env import get_secret, load_env
from bot.exchange.binance_rest import BinanceRestClient

if os.path.isdir("/app"):
    os.chdir("/app")


def pick_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


load_env()

key = get_secret("BINANCE_TESTNET_API_KEY", required=False) or pick_env("BINANCE_TESTNET_API_KEY", "BINANCE_API_KEY")
secret = get_secret("BINANCE_TESTNET_API_SECRET", required=False) or pick_env("BINANCE_TESTNET_API_SECRET", "BINANCE_API_SECRET")
mode = "testnet"

print("ENV_KEYS_PRESENT:", {
    "BINANCE_TESTNET_API_KEY": bool(os.environ.get("BINANCE_TESTNET_API_KEY")),
    "BINANCE_TESTNET_API_SECRET": bool(os.environ.get("BINANCE_TESTNET_API_SECRET")),
    "BINANCE_API_KEY": bool(os.environ.get("BINANCE_API_KEY")),
    "BINANCE_API_SECRET": bool(os.environ.get("BINANCE_API_SECRET")),
})
print("GOOGLE_CLOUD_PROJECT:", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))

if not key or not secret:
    print("NO_API_KEYS_FOUND")
else:
    client = BinanceRestClient(api_key=key, api_secret=secret, mode=mode)
    print("BALANCE:", client.get_balance())
    for symbol in ["SOLUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT"]:
        try:
            pos = client.get_position(symbol)
            print("POSITION:", symbol, pos)
        except Exception as exc:
            print("POSITION_ERR:", symbol, str(exc))
    try:
        if client._client is not None:
            print("OPEN_ORDERS_SOL:", client._client.futures_get_open_orders(symbol="SOLUSDT"))
    except Exception as exc:
        print("OPEN_ORDERS_SOL_ERR:", str(exc))

for db_path in ["/app/data/paper_trades.db", "data/paper_trades.db"]:
    if os.path.exists(db_path):
        print("DB_PATH:", db_path)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        print("FILLS_COUNT:", cur.execute("select count(*) from fills").fetchone()[0])
        print("TRADES_COUNT:", cur.execute("select count(*) from trades").fetchone()[0])
        print("RECENT_FILLS:", cur.execute("select timestamp, strategy, symbol, side, quantity, price, source from fills order by id desc limit 10").fetchall())
        conn.close()
        break
else:
    print("DB_NOT_FOUND")
