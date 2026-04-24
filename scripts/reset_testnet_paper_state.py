from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from bot.config.env import get_secret, load_env
from bot.exchange.binance_rest import BinanceRestClient
from bot.runtime.paper_reset import clear_runtime_state, flatten_testnet_state


def archive_journal(db_path: Path) -> None:
    if not db_path.exists():
        print(f"journal_missing path={db_path}")
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.stem}.{stamp}.bak{db_path.suffix}")
    db_path.rename(target)
    print(f"journal_archived path={target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-journal", action="store_true")
    parser.add_argument("--journal-path", default="data/paper_trades.db")
    args = parser.parse_args()

    load_env()
    api_key = get_secret("BINANCE_TESTNET_API_KEY")
    api_secret = get_secret("BINANCE_TESTNET_API_SECRET")
    client = BinanceRestClient(api_key=api_key, api_secret=api_secret, mode="testnet")

    exchange_result = flatten_testnet_state(client)
    if exchange_result.balance_before is not None:
        print(f"available_balance_before={exchange_result.balance_before:.8f}")
    print(f"orders_cancelled={exchange_result.canceled_orders}")
    print(f"positions_closed={exchange_result.closed_positions}")
    for error in exchange_result.errors:
        print(f"reset_error={error}")

    if args.archive_journal:
        runtime_result = clear_runtime_state(journal_path=args.journal_path, archive_journal=True)
        if runtime_result.archived_journal:
            print(f"journal_archived path={runtime_result.archived_journal}")
        for error in runtime_result.errors:
            print(f"runtime_reset_error={error}")

    if exchange_result.balance_after is not None:
        print(f"available_balance_after={exchange_result.balance_after:.8f}")


if __name__ == "__main__":
    main()