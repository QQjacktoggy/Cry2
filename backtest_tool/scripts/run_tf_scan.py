#!/usr/bin/env python3
"""Run timeframe cross-validation for a strategy.

Tests each strategy against multiple timeframes to find the optimal one.

Usage:
    python -m backtest_tool.scripts.run_tf_scan \\
        --strategy trend_donchian \\
        --symbol BTCUSDT \\
        --start 2024-04-17 \\
        --end 2026-04-17
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore  # noqa: E402
from backtest_tool.engine import BacktestRunner  # noqa: E402
from backtest_tool.strategies import STRATEGY_MAP  # noqa: E402

# Recommended timeframes per strategy
STRATEGY_TIMEFRAMES: dict[str, list[str]] = {
    "trend_donchian": ["1h", "2h", "4h", "8h"],
    "mean_reversion_bb": ["15m", "30m", "1h", "4h"],
    "grid_futures": ["1h", "2h", "4h", "8h"],
    "funding_arb": ["4h", "8h"],
    "momentum_reversal": ["1d"],
}


def main() -> None:
    """Run timeframe scan."""
    parser = argparse.ArgumentParser(description="Timeframe cross-validation")
    parser.add_argument("--strategy", type=str, required=True, choices=list(STRATEGY_MAP.keys()))
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--target", type=str, default="sharpe_ratio")
    parser.add_argument("--timeframes", nargs="+", default=None)
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
    store = DataStore()
    runner = BacktestRunner(config_path=str(config_path))
    if args.capital:
        runner.initial_capital = args.capital

    timeframes = args.timeframes or STRATEGY_TIMEFRAMES.get(args.strategy, ["4h"])
    strategy_cls = STRATEGY_MAP[args.strategy]

    print(f"\n📊 Timeframe Scan: {args.strategy} × {args.symbol}")
    print(f"   Timeframes: {timeframes}")
    print("=" * 80)

    results = []
    for tf in timeframes:
        try:
            print(f"\n⏱️  Testing {tf}...", end=" ")
            df = store.load_klines(args.symbol, tf, start=args.start, end=args.end)
            if df.empty:
                print("⚠️  No data")
                continue

            strategy = strategy_cls()
            strategy.required_timeframe = tf
            result = runner.run_single(strategy, df, symbol=args.symbol, timeframe=tf)

            m = result.metrics
            row = {
                "timeframe": tf,
                "bars": len(df),
                "sharpe_ratio": m.get("sharpe_ratio", 0),
                "total_return": m.get("total_return", 0),
                "max_drawdown": m.get("max_drawdown", 0),
                "total_trades": m.get("total_trades", 0),
                "win_rate": m.get("win_rate", 0),
                "calmar_ratio": m.get("calmar_ratio", 0),
            }
            results.append(row)
            print(
                f"✅ Sharpe={row['sharpe_ratio']:.4f}  "
                f"Return={row['total_return']:+.2f}%  "
                f"MaxDD={row['max_drawdown']:.2f}%  "
                f"Trades={row['total_trades']:.0f}"
            )
        except Exception as e:
            print(f"❌ Error: {e}")

    if not results:
        print("\n⚠️  No results to compare.")
        return

    results.sort(key=lambda x: x.get(args.target, 0), reverse=True)

    print(f"\n{'=' * 80}")
    print(f"🏆 Ranking by {args.target}")
    print(f"{'=' * 80}")
    print(
        f"{'Rank':<5} {'TF':<6} {'Sharpe':>8} {'Return%':>9} {'MaxDD%':>9} "
        f"{'Trades':>7} {'WinRate%':>9} {'Calmar':>8}"
    )
    print("-" * 80)

    for i, row in enumerate(results, 1):
        print(
            f"{i:<5} {row['timeframe']:<6} {row['sharpe_ratio']:>8.4f} "
            f"{row['total_return']:>+9.2f} {row['max_drawdown']:>9.2f} "
            f"{row['total_trades']:>7.0f} {row['win_rate']:>9.2f} "
            f"{row['calmar_ratio']:>8.4f}"
        )

    best = results[0]
    print(
        f"\n✅ Best timeframe for {args.strategy} on {args.symbol}: "
        f"{best['timeframe']} ({args.target}={best[args.target]:.4f})"
    )


if __name__ == "__main__":
    main()
