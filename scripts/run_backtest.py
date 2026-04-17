#!/usr/bin/env python3
"""Run a backtest with configured strategies."""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog

from bot.backtest.engine import BacktestEngine
from bot.backtest.metrics import MetricsCalculator
from bot.backtest.report import ReportGenerator
from bot.config.env import load_env
from bot.config.loader import load_config
from bot.core.logger import setup_logging
from bot.data.storage import ParquetStorage
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.strategy.registry import StrategyRegistry, register_default_strategies
from bot.utils.time_utils import datetime_to_ms, parse_date

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--env", default="backtest", help="Environment (default: backtest)")
    parser.add_argument("--config", default="config/config.yaml", help="Config file path")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--strategy", help="Run specific strategy only")
    args = parser.parse_args()

    # Load config
    load_env()
    config = load_config(args.config, environment=args.env)
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    # Register strategies
    register_default_strategies()

    # Parse dates
    start_date = args.start or config.get("start_date", "2024-01-01")
    end_date = args.end or config.get("end_date", "2025-12-31")
    start_ms = datetime_to_ms(parse_date(start_date))
    end_ms = datetime_to_ms(parse_date(end_date))

    # Create strategies
    strategy_configs = config.get("strategies", {})
    if args.strategy:
        strategy_configs = {args.strategy: strategy_configs.get(args.strategy, {})}

    strategies = StrategyRegistry.create_all(strategy_configs)
    if not strategies:
        logger.error("no_strategies_created")
        return

    # Get all symbols from strategies
    all_symbols = list(set(sym for s in strategies for sym in s.symbols))

    # Execution config
    exec_cfg = config.get("execution", {})
    slippage = SlippageModel(
        model_type=exec_cfg.get("slippage_model", "fixed_bps"),
        fixed_bps=exec_cfg.get("slippage_bps", 2),
        bps_base=exec_cfg.get("slippage_bps_base", 1),
    )
    fees = FeeModel(
        maker_rate=exec_cfg.get("fee_rate_maker", 0.0002),
        taker_rate=exec_cfg.get("fee_rate_taker", 0.0004),
    )

    # Run backtest
    engine = BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        timeframe=strategies[0].timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=config.get("initial_capital", 10000),
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=slippage,
        fee_model=fees,
    )

    results = engine.run()

    # Calculate metrics
    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=config.get("initial_capital", 10000),
    )
    metrics = calc.calculate_all()

    # Print results
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Run ID:           {results['run_id']}")
    print(f"Period:           {start_date} → {end_date}")
    print(f"Symbols:          {', '.join(all_symbols)}")
    print(f"Initial Capital:  {config.get('initial_capital', 10000):.2f} USDT")
    print(f"Final Equity:     {metrics['final_equity']:.2f} USDT")
    print(f"Total Return:     {metrics['total_return']:.2%}")
    print(f"Ann. Return:      {metrics['annualized_return']:.2%}")
    print(f"Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown_pct']:.2%}")
    print(f"Win Rate:         {metrics['win_rate']:.1%}")
    print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"Total Trades:     {metrics['total_trades']}")
    print("=" * 60)

    # Generate report
    report = ReportGenerator()
    report_path = report.generate(
        metrics=metrics,
        run_id=results["run_id"],
        output_dir=config.get("paths", {}).get("results_dir", "./data/backtest_results"),
    )
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
