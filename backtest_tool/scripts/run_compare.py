#!/usr/bin/env python3
"""Run multi-strategy comparison and generate HTML comparison report.

Usage:
    python -m backtest_tool.scripts.run_compare \\
        --strategies trend_donchian mean_reversion_bb grid_futures \\
        --symbol BTCUSDT \\
        --timeframes 4h 1h 4h \\
        --start 2023-01-01
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner
from backtest_tool.reports import HTMLReportGenerator
from backtest_tool.strategies import STRATEGY_MAP


def main():
    parser = argparse.ArgumentParser(description="Multi-strategy comparison backtest")
    parser.add_argument("--strategies", nargs="+", required=True,
                        choices=list(STRATEGY_MAP.keys()),
                        help="Strategy names to compare")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol")
    parser.add_argument("--timeframes", nargs="+", required=True,
                        help="Timeframe for each strategy (same order)")
    parser.add_argument("--start", type=str, default=None, help="Start date")
    parser.add_argument("--end", type=str, default=None, help="End date")
    parser.add_argument("--capital", type=float, default=None, help="Initial capital")
    parser.add_argument("--output", type=str, default=None, help="Output filename")
    args = parser.parse_args()

    if len(args.strategies) != len(args.timeframes):
        parser.error("Number of strategies must match number of timeframes")

    # Load config
    config_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

    store = DataStore()
    runner = BacktestRunner(config_path=str(config_path))
    if args.capital:
        runner.initial_capital = args.capital
    results = []

    for strat_name, tf in zip(args.strategies, args.timeframes):
        print(f"\n📂 Loading {args.symbol} {tf}...")
        df = store.load_klines(args.symbol, tf, start=args.start, end=args.end)
        print(f"   ✅ {len(df)} rows loaded")

        strategy_cls = STRATEGY_MAP[strat_name]
        strategy = strategy_cls()

        print(f"🚀 Running {strat_name}...")
        result = runner.run_single(strategy, df, symbol=args.symbol, timeframe=tf)
        results.append(result)

        m = result.metrics
        print(f"   Return: {m.get('total_return', 0):+.2f}%  "
              f"Sharpe: {m.get('sharpe_ratio', 0):.4f}  "
              f"Max DD: {m.get('max_drawdown', 0):.2f}%")

    # Generate comparison report
    print(f"\n📝 Generating comparison report for {len(results)} strategies...")
    reporter = HTMLReportGenerator()
    output_path = reporter.generate_comparison(results, filename=args.output)
    print(f"✅ Report saved: {output_path}")


if __name__ == "__main__":
    main()
