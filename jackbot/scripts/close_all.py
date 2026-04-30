"""Script to close all positions and orders for the bot symbols."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add src/ to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml
from dotenv import load_dotenv
from jackbot.exchange.client import BinanceClient


def load_config(path: str = "config/settings.yaml") -> dict:
    config_path = ROOT / path
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    load_dotenv(ROOT / ".env")
    config = load_config()
    
    exchange = config.get("exchange", {})
    trading = config.get("trading", {})
    symbols = trading.get("symbols", ["BTCUSDT", "ETHUSDT"])
    testnet = exchange.get("mode", "testnet") == "testnet"

    client = BinanceClient(
        api_key=os.getenv(exchange.get("api_key_env", ""), ""),
        api_secret=os.getenv(exchange.get("api_secret_env", ""), ""),
        testnet=testnet,
        base_url=exchange.get("base_url", ""),
    )
    
    client.load_symbol_info(symbols)

    print(f"🛑 Starting emergency close for: {symbols}")

    for symbol in symbols:
        # 1. Cancel all orders
        print(f"[{symbol}] Cancelling all orders...")
        client.cancel_all_orders(symbol)
        
        # 2. Market close position
        print(f"[{symbol}] Checking position...")
        pos = client.get_position(symbol)
        amt = float(pos.get("positionAmt", 0))
        
        if abs(amt) > 0:
            side = "SELL" if amt > 0 else "BUY"
            print(f"[{symbol}] Closing {amt} via {side} market order...")
            client.place_market_order(
                symbol=symbol,
                side=side,
                quantity=abs(amt),
                reduce_only=True
            )
        else:
            print(f"[{symbol}] No open position.")

    print("✅ All positions and orders cleared.")


if __name__ == "__main__":
    main()
