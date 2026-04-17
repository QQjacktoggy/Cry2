#!/usr/bin/env python3
"""Run portfolio optimization across multiple strategies.

Usage:
    python -m backtest_tool.scripts.run_optimize \\
        --strategies trend_donchian mean_reversion_bb grid_futures \\
        --symbol BTCUSDT \\
        --timeframes 4h 1h 4h \\
        --start 2023-01-01 \\
        --target sharpe_ratio
"""

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner, PortfolioOptimizer
from backtest_tool.reports import HTMLReportGenerator
from backtest_tool.strategies import STRATEGY_MAP


def main():
    parser = argparse.ArgumentParser(description="Portfolio optimization")
    parser.add_argument("--strategies", nargs="+", required=True,
                        choices=list(STRATEGY_MAP.keys()))
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--timeframes", nargs="+", required=True)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--target", type=str, default="sharpe_ratio",
                        help="Optimization target (sharpe_ratio, calmar_ratio, total_return)")
    parser.add_argument("--step", type=float, default=0.05, help="Allocation step size (default: 0.05)")
    parser.add_argument("--max-weight", type=float, default=0.80,
                        help="Max weight per strategy (default: 0.80)")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if len(args.strategies) != len(args.timeframes):
        parser.error("Number of strategies must match number of timeframes")

    # Load config
    config_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

    store = DataStore()
    runner = BacktestRunner(config_path=str(config_path))
    if args.capital:
        runner.initial_capital = args.capital
    results_dict = {}
    results_list = []

    for strat_name, tf in zip(args.strategies, args.timeframes):
        print(f"\n📂 {strat_name} ({tf})...")
        df = store.load_klines(args.symbol, tf, start=args.start, end=args.end)

        strategy_cls = STRATEGY_MAP[strat_name]
        strategy = strategy_cls()

        result = runner.run_single(strategy, df, symbol=args.symbol, timeframe=tf)
        results_dict[strat_name] = result
        results_list.append(result)
        print(f"   ✅ Return: {result.metrics.get('total_return', 0):+.2%}")

    # Optimize
    print(f"\n🔧 Running portfolio optimization (target: {args.target})...")
    optimizer = PortfolioOptimizer()
    opt_result = optimizer.optimize(
        results_dict,
        allocation_step=args.step,
        max_allocation=args.max_weight,
        target_metric=args.target,
    )

    print(f"\n{'='*60}")
    print(f"🏆 Optimal Allocation ({args.target})")
    print(f"{'='*60}")

    top = opt_result["top_allocations"][:5]
    for i, alloc in enumerate(top, 1):
        weights = alloc["allocation"]
        target_val = alloc[args.target]
        weights_str = "  ".join(f"{k}: {v:.0%}" for k, v in weights.items())
        print(f"  #{i}: {args.target}={target_val:.4f}  |  {weights_str}")

    # Generate comparison report with optimization
    print(f"\n📝 Generating report...")
    reporter = HTMLReportGenerator()
    output_path = reporter.generate_comparison(
        results_list, filename=args.output, optimization_result=opt_result
    )
    print(f"✅ Report saved: {output_path}")


if __name__ == "__main__":
    main()
