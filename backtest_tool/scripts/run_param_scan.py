#!/usr/bin/env python3
"""Run parameter scan for a strategy and generate report.

Usage:
    python -m backtest_tool.scripts.run_param_scan \\
        --strategy trend_donchian \\
        --symbol BTCUSDT \\
        --timeframe 4h \\
        --start 2023-01-01

    python -m backtest_tool.scripts.run_param_scan \\
        --strategy mean_reversion_bb \\
        --symbol ETHUSDT \\
        --timeframe 1h \\
        --target sharpe_ratio \\
        --top 20
"""

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner, ParamScanner
from backtest_tool.strategies import STRATEGY_MAP


def main():
    parser = argparse.ArgumentParser(description="Parameter scan for a strategy")
    parser.add_argument("--strategy", type=str, required=True,
                        choices=list(STRATEGY_MAP.keys()))
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--timeframe", type=str, required=True)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--target", type=str, default="sharpe_ratio",
                        help="Optimization target metric")
    parser.add_argument("--top", type=int, default=10, help="Number of top results to show")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Save results to JSON file")
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

    # Load data
    store = DataStore()
    print(f"📂 Loading {args.symbol} {args.timeframe}...")
    df = store.load_klines(args.symbol, args.timeframe, start=args.start, end=args.end)
    print(f"   ✅ {len(df)} rows")

    # Load param spaces
    param_spaces_path = Path(__file__).resolve().parent.parent / "config" / "param_spaces.yaml"
    with open(param_spaces_path) as f:
        all_param_spaces = yaml.safe_load(f)

    param_space = all_param_spaces.get("param_spaces", all_param_spaces).get(args.strategy, {})
    if not param_space:
        print(f"❌ No param space defined for {args.strategy} in param_spaces.yaml")
        sys.exit(1)

    # Run scan
    runner = BacktestRunner(config_path=str(config_path))
    if args.capital:
        runner.initial_capital = args.capital

    strategy_cls = STRATEGY_MAP[args.strategy]
    scanner = ParamScanner(runner)

    print(f"\n🔍 Scanning parameters for {args.strategy}...")
    print(f"   Param space: {param_space}")

    results_df = scanner.scan(strategy_cls, param_space, df,
                              sort_by=args.target, top_n=args.top)

    # Display results
    print(f"\n{'='*70}")
    print(f"🏆 Top {args.top} by {args.target}")
    print(f"{'='*70}")

    if results_df.empty:
        print("   No results found.")
    else:
        for rank, row in results_df.iterrows():
            target_val = row.get(args.target, 0)
            # Extract param columns (everything not in known metrics)
            metric_keys = {"total_return", "annualized_return", "sharpe_ratio", "sortino_ratio",
                          "max_drawdown", "calmar_ratio", "total_trades", "win_rate",
                          "avg_trade_pnl", "profit_factor", "volatility_ann",
                          "max_dd_duration_bars", "avg_win", "avg_loss", "best_trade",
                          "worst_trade", "payoff_ratio", "net_profit", "total_fees"}
            param_cols = {k: v for k, v in row.items() if k not in metric_keys}
            print(f"\n  #{rank}: {args.target}={target_val:.4f}")
            print(f"      Params: {param_cols}")
            print(f"      Return: {row.get('total_return', 0):+.2%}  "
                  f"Sharpe: {row.get('sharpe_ratio', 0):.4f}  "
                  f"MaxDD: {row.get('max_drawdown', 0):.2%}  "
                  f"Trades: {row.get('total_trades', 0):.0f}")

    # Save to JSON
    if args.output_json:
        output_path = Path(args.output_json)
        results_df.to_json(output_path, orient="records", indent=2)
        print(f"\n💾 Results saved: {output_path}")


if __name__ == "__main__":
    main()
