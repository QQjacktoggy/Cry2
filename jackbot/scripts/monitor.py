"""Jackbot_V1 — Live monitor script.

Run inside the jackbot-v1 container to check current trading state:
  docker exec jackbot-v1 python scripts/monitor.py
  docker exec jackbot-v1 python scripts/monitor.py --watch   # refresh every 30s
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from jackbot.exchange.client import BinanceClient

VM_ZONE = "asia-east1-b"
VM_NAME = "instance-20260424-060848"
CONTAINER = "jackbot-v1"


def _sep(label: str = "") -> None:
    width = 60
    if label:
        pad = (width - len(label) - 2) // 2
        print("─" * pad + f" {label} " + "─" * (width - pad - len(label) - 2))
    else:
        print("─" * width)


def _client() -> BinanceClient:
    key = os.getenv("BINANCE_TESTNET_API_KEY", "")
    secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
    if not key or not secret:
        print("[ERROR] BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET 未設定")
        sys.exit(1)
    return BinanceClient(api_key=key, api_secret=secret, testnet=True)


def show_status(client: BinanceClient, symbols: list[str]) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'─'*60}")
    print(f"  Jackbot_V1 即時監控   {now}")
    print(f"{'─'*60}")

    # Latency
    try:
        latency = client.ping()
        print(f"  連線延遲: {latency} ms")
    except Exception as e:
        print(f"  [WARN] ping 失敗: {e}")

    # Balance
    _sep("帳戶餘額")
    try:
        balance = client.get_balance()
        print(f"  可用 USDT: {balance:.4f}")
    except Exception as e:
        print(f"  [ERROR] 餘額查詢失敗: {e}")

    # Positions
    _sep("持倉")
    has_position = False
    for sym in symbols:
        try:
            pos = client.get_position(sym)
            amt = float(pos.get("positionAmt", 0))
            if abs(amt) < 1e-9:
                continue
            has_position = True
            entry = float(pos.get("entryPrice", 0))
            upnl = float(pos.get("unrealizedProfit", 0))
            lev = pos.get("leverage", "?")
            side = "LONG" if amt > 0 else "SHORT"
            print(
                f"  {sym}: {side} {abs(amt):.4f}"
                f"  進場={entry:.2f}  槓桿={lev}x"
                f"  浮盈={upnl:+.4f} USDT"
            )
        except Exception as e:
            print(f"  [ERROR] {sym} 持倉查詢失敗: {e}")
    if not has_position:
        print("  (無持倉)")

    # Open orders
    _sep("掛單")
    total_orders = 0
    for sym in symbols:
        try:
            orders = client.get_open_orders(sym)
            if not orders:
                continue
            total_orders += len(orders)
            print(f"\n  {sym} — {len(orders)} 個掛單:")
            for o in orders:
                side = o.get("side", "?")
                price = float(o.get("price", 0))
                qty = float(o.get("origQty", 0))
                oid = o.get("orderId", "?")
                cid = o.get("clientOrderId", "")
                cid_short = cid[:20] + "…" if len(cid) > 20 else cid
                print(f"    [{oid}] {side} {qty:.4f} @ {price:.2f}  cid={cid_short}")
        except Exception as e:
            print(f"  [ERROR] {sym} 掛單查詢失敗: {e}")
    if total_orders == 0:
        print("  (無掛單)")

    # Recent fills (last 10 from futures account trades)
    _sep("近期成交 (最近 10 筆)")
    any_fill = False
    for sym in symbols:
        try:
            trades = client._signed_get(
                "/fapi/v1/userTrades",
                {"symbol": sym, "limit": 5},
            )
            if not trades:
                continue
            any_fill = True
            print(f"\n  {sym}:")
            for t in reversed(trades):
                ts = datetime.fromtimestamp(t["time"] / 1000, tz=UTC).strftime("%H:%M:%S")
                side = t.get("side", "?")
                price = float(t.get("price", 0))
                qty = float(t.get("qty", 0))
                realized = float(t.get("realizedPnl", 0))
                maker = "M" if t.get("maker") else "T"
                print(
                    f"    {ts}  {side} {qty:.4f} @ {price:.2f}"
                    f"  PnL={realized:+.4f}  [{maker}]"
                )
        except Exception as e:
            print(f"  [ERROR] {sym} 成交查詢失敗: {e}")
    if not any_fill:
        print("  (無記錄)")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Jackbot_V1 live monitor")
    parser.add_argument(
        "--watch", action="store_true", help="持續刷新 (每 30 秒)"
    )
    parser.add_argument(
        "--interval", type=int, default=30, help="刷新間隔秒數 (預設 30)"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT"],
        help="監控的交易對 (預設 BTCUSDT ETHUSDT)",
    )
    args = parser.parse_args()

    client = _client()
    client.load_symbol_info(args.symbols)

    try:
        if args.watch:
            print(f"持續監控模式，每 {args.interval} 秒刷新。按 Ctrl+C 結束。")
            while True:
                show_status(client, args.symbols)
                time.sleep(args.interval)
        else:
            show_status(client, args.symbols)
    except KeyboardInterrupt:
        print("\n監控已停止。")
    finally:
        client.close()


if __name__ == "__main__":
    main()
