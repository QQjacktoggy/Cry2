#!/usr/bin/env python3
"""批次執行 54 個策略（4 baseline + 50 new）於合成/本地資料，輸出總結 CSV。

Usage:
    python -m backtest_tool.scripts.run_all_50
    python -m backtest_tool.scripts.run_all_50 --symbol BTCUSDT --start 2024-04-17
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT
from backtest_tool.scripts.optimize_top10 import _generate_synthetic_btc, _load_or_generate

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"


def _timeframe_for(strategy_name: str) -> str:
    from backtest_tool.strategies import STRATEGY_MAP as SM
    cls = SM[strategy_name]
    return getattr(cls, "required_timeframe", "4h")


def main() -> None:
    parser = argparse.ArgumentParser(description="批次執行 54 策略回測")
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--start", type=str, default="2024-04-17")
    parser.add_argument("--end", type=str, default="2026-04-17")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--save-csv", type=str,
                        default=str(Path(__file__).resolve().parent.parent / "reports" / "output" / "strategies_50_results.csv"))
    args = parser.parse_args()

    store = DataStore()
    runner = BacktestRunner(config_path=str(CONFIG_PATH))
    runner.initial_capital = args.capital

    results: list[dict] = []
    data_cache: dict[str, pd.DataFrame] = {}

    print("=" * 100)
    print(f"  批次執行 {len(STRATEGY_MAP)} 個策略")
    print(f"  標的：{args.symbol} │ 期間：{args.start} → {args.end} │ 資金：${args.capital:,.0f}")
    print("=" * 100)

    for i, (name, cls) in enumerate(sorted(STRATEGY_MAP.items()), 1):
        tf = _timeframe_for(name)
        print(f"  [{i:>2}/{len(STRATEGY_MAP)}] {name:<35} tf={tf}", end=" ")

        # Load/cache data per timeframe
        if tf not in data_cache:
            df = _load_or_generate(store, args.symbol, tf, args.start, args.end)
            if df.empty or len(df) < 100:
                print("❌ 資料不足")
                continue
            data_cache[tf] = df
        df = data_cache[tf]

        # Prepare funding data if needed
        if name in ("funding_arb", "funding_arb_relaxed", "spot_perp_basis"):
            try:
                funding = store.load_funding(args.symbol, start=args.start, end=args.end)
                if funding is not None and not funding.empty:
                    df = FundingArbVBT.prepare_data(df, funding)
            except Exception:
                pass

        try:
            strategy = cls()
            result = runner.run_single(strategy, df, symbol=args.symbol, timeframe=tf)
            m = result.metrics
            row = {
                "strategy": name,
                "symbol": args.symbol,
                "timeframe": tf,
                "total_return": m.get("total_return", 0.0),
                "annual_return": m.get("annual_return", 0.0),
                "sharpe_ratio": m.get("sharpe_ratio", 0.0),
                "max_drawdown": m.get("max_drawdown", 0.0),
                "calmar_ratio": m.get("calmar_ratio", 0.0),
                "win_rate": m.get("win_rate", 0.0),
                "total_trades": m.get("total_trades", 0),
                "profit_factor": m.get("profit_factor", 0.0),
                "final_value": m.get("final_value", 0.0),
            }
            results.append(row)
            print(
                f"return={row['total_return']:+6.2f}% "
                f"sharpe={row['sharpe_ratio']:+5.2f} "
                f"dd={row['max_drawdown']:+6.2f}% "
                f"trades={row['total_trades']}"
            )
        except Exception as e:
            print(f"❌ 錯誤：{e}")
            results.append({
                "strategy": name,
                "symbol": args.symbol,
                "timeframe": tf,
                "error": str(e)[:200],
            })

    if not results:
        print("\n  ⚠️  無任何結果")
        return

    df_res = pd.DataFrame(results)
    Path(args.save_csv).parent.mkdir(parents=True, exist_ok=True)
    df_res.to_csv(args.save_csv, index=False, encoding="utf-8-sig")
    print(f"\n  💾 結果已儲存：{args.save_csv}")

    # Top 20 by Sharpe
    if "sharpe_ratio" in df_res.columns:
        df_ok = df_res.dropna(subset=["sharpe_ratio"]).copy()
        df_ok = df_ok[df_ok["total_trades"].fillna(0) > 0]
        df_ok = df_ok.sort_values("sharpe_ratio", ascending=False).head(20)

        print("\n  🏆 Top 20 by Sharpe:")
        print("  " + "-" * 98)
        print(f"  {'Rank':>4}  {'Strategy':<32}  {'TF':>4}  "
              f"{'Return%':>9}  {'Sharpe':>7}  {'MaxDD%':>8}  {'Trades':>6}  {'PF':>6}")
        print("  " + "-" * 98)
        for rank, (_, row) in enumerate(df_ok.iterrows(), 1):
            print(
                f"  {rank:>4}  {row['strategy']:<32}  {row['timeframe']:>4}  "
                f"{row.get('total_return', 0):>+8.2f}%  "
                f"{row.get('sharpe_ratio', 0):>7.3f}  "
                f"{row.get('max_drawdown', 0):>8.2f}  "
                f"{int(row.get('total_trades', 0)):>6}  "
                f"{row.get('profit_factor', 0):>6.2f}"
            )

    print("\n  ✅ 完成！")


if __name__ == "__main__":
    main()
