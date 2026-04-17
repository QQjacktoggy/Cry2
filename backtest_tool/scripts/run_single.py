#!/usr/bin/env python3
"""Run a single strategy backtest and generate HTML report.

Usage:
    python -m backtest_tool.scripts.run_single \\
        --strategy trend_donchian \\
        --symbol BTCUSDT \\
        --timeframe 4h \\
        --start 2023-01-01 \\
        --end 2024-12-31

    python -m backtest_tool.scripts.run_single \\
        --strategy mean_reversion_bb \\
        --symbol ETHUSDT \\
        --timeframe 1h \\
        --capital 50000
"""

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner
from backtest_tool.reports import HTMLReportGenerator
from backtest_tool.strategies import STRATEGY_MAP


def main():
    parser = argparse.ArgumentParser(description="Run single strategy backtest")
    parser.add_argument("--strategy", type=str, required=True,
                        choices=list(STRATEGY_MAP.keys()),
                        help="Strategy name")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--timeframe", type=str, required=True, help="Timeframe (e.g., 1h, 4h)")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=None, help="Initial capital (overrides config)")
    parser.add_argument("--params", type=str, default=None,
                        help="Strategy params as JSON or YAML string")
    parser.add_argument("--output", type=str, default=None, help="Output filename (without .html)")
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
    initial_capital = args.capital  # will override in run_single if set

    # Create runner from config
    runner = BacktestRunner(config_path=str(config_path))
    if args.capital:
        runner.initial_capital = args.capital

    # Load data
    store = DataStore()
    print(f"📂 Loading {args.symbol} {args.timeframe} data...")
    df = store.load_klines(args.symbol, args.timeframe, start=args.start, end=args.end)
    print(f"   ✅ {len(df)} rows loaded ({df.index[0]} → {df.index[-1]})")

    # Strategy params
    strategy_params = {}
    if args.params:
        strategy_params = yaml.safe_load(args.params)

    # Create strategy
    strategy_cls = STRATEGY_MAP[args.strategy]
    strategy = strategy_cls(**strategy_params)

    # Run backtest
    print(f"\n🚀 Running {args.strategy} backtest...")
    result = runner.run_single(strategy, df, symbol=args.symbol, timeframe=args.timeframe,
                               initial_capital=args.capital)

    # Print summary
    m = result.metrics
    print(f"\n{'='*50}")
    print(f"📊 {args.strategy} 回測結果")
    print(f"{'='*50}")
    print(f"   總收益率:     {m.get('total_return', 0):+.2f}%")
    print(f"   年化收益率:   {m.get('annual_return', 0):+.2f}%")
    print(f"   Sharpe Ratio: {m.get('sharpe_ratio', 0):.4f}")
    print(f"   最大回撤:     {m.get('max_drawdown', 0):.2f}%")
    print(f"   勝率:         {m.get('win_rate', 0):.1f}%")
    print(f"   總交易:       {m.get('total_trades', 0)}")
    print(f"   最終價值:     ${m.get('final_value', 0):,.2f}")
    print(f"{'='*50}")

    # Generate report
    print("\n📝 Generating HTML report...")
    reporter = HTMLReportGenerator()
    output_path = reporter.generate_single(result, filename=args.output)
    print(f"✅ Report saved: {output_path}")


if __name__ == "__main__":
    main()
