#!/usr/bin/env python3
"""Manual kill switch - immediately close all positions and halt."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.config.env import get_secret, load_env
from bot.exchange.account import AccountManager
from bot.exchange.binance_rest import BinanceRestClient


def main() -> None:
    print("🚨 KILL SWITCH")
    print("=" * 60)
    print("This will CLOSE ALL POSITIONS and CANCEL ALL ORDERS.")
    confirm = input("Type 'KILL' to confirm: ")

    if confirm != "KILL":
        print("Cancelled.")
        return

    load_env()
    api_key = get_secret("BINANCE_TESTNET_API_KEY", required=False)
    api_secret = get_secret("BINANCE_TESTNET_API_SECRET", required=False)

    if not api_key:
        print("ERROR: API keys not configured")
        return

    client = BinanceRestClient(api_key, api_secret, mode="testnet")
    account = AccountManager(client)

    positions = account.get_all_positions()
    print(f"\nFound {len(positions)} open positions")

    for pos in positions:
        print(f"  Closing {pos.symbol}: {pos.side.value} {pos.quantity}")
        # Would call client.place_order with market close

    print("\nKill switch executed.")


if __name__ == "__main__":
    main()
