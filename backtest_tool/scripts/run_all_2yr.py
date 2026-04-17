#!/usr/bin/env python3
"""Run 2-year backtest for all strategies across all symbols."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner
from backtest_tool.reports import HTMLReportGenerator
from backtest_tool.strategies import STRATEGY_MAP
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT

store = DataStore()
config_path = str(Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml")
runner = BacktestRunner(config_path=config_path)
runner.initial_capital = 150.0

START = "2024-04-17"
END = "2026-04-17"

strat_tfs = {
    "trend_donchian": "4h",
    "mean_reversion_bb": "1h",
    "grid_futures": "4h",
    "funding_arb": "8h",
}

symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
reporter = HTMLReportGenerator()
all_results = []

for symbol in symbols:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  {symbol}")
    print(sep)

    symbol_results = []
    for strat_name, tf in strat_tfs.items():
        print(f"\n  [{strat_name}] ({tf})")

        df = store.load_klines(symbol, tf, start=START, end=END)
        if df.empty or len(df) < 50:
            print(f"    ❌ Data insufficient: {len(df)} rows")
            continue

        strategy = STRATEGY_MAP[strat_name]()

        if strat_name == "funding_arb":
            funding = store.load_funding(symbol, start=START, end=END)
            if funding.empty:
                print("    ⚠️ No funding data, skipping")
                continue
            df = FundingArbVBT.prepare_data(df, funding)

        result = runner.run_single(strategy, df, symbol=symbol, timeframe=tf)
        symbol_results.append(result)
        all_results.append(result)

        m = result.metrics
        print(f"    💰 Return: {m.get('total_return', 0):+.2f}%")
        print(f"    📈 Annual: {m.get('annual_return', 0):+.2f}%")
        print(f"    📊 Sharpe: {m.get('sharpe_ratio', 0):.4f}")
        print(f"    📉 Max DD: {m.get('max_drawdown', 0):.2f}%")
        print(f"    🎯 Win:    {m.get('win_rate', 0):.1f}%")
        print(f"    🔢 Trades: {m.get('total_trades', 0)}")
        print(f"    💵 Final:  ${m.get('final_value', 0):,.2f}")

    if len(symbol_results) > 1:
        output = reporter.generate_comparison(symbol_results, filename=f"compare_{symbol}_2yr")
        print(f"\n  📝 Report: {output}")

sep = "=" * 60
print(f"\n{sep}")
print("  ALL RESULTS SUMMARY")
print(sep)

if all_results:
    output = reporter.generate_comparison(all_results, filename="compare_ALL_2yr")
    print(f"  📝 Full report: {output}")

print("\n✅ Done!")
