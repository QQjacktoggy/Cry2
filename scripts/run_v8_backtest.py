#!/usr/bin/env python3
"""Run the V7.4 control baseline or a V8 phase-1 candidate profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
from bot.utils.time_utils import datetime_to_ms, parse_date
from strategies_lab.integration import build_v8_strategy_suite, get_profile_preset

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V8 profile backtest")
    parser.add_argument(
        "--profile",
        default="baseline",
        choices=["baseline", "v8a", "v8b", "v8c", "v8d"],
        help="Profile to run",
    )
    parser.add_argument("--env", default="backtest", help="Environment (default: backtest)")
    parser.add_argument("--config", default="config\\config.yaml", help="Config file path")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment=args.env)
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    start_date = args.start or config.get("start_date", "2024-01-01")
    end_date = args.end or config.get("end_date", "2025-12-31")
    start_ms = datetime_to_ms(parse_date(start_date))
    end_ms = datetime_to_ms(parse_date(end_date))

    strategies = build_v8_strategy_suite(initial_capital=args.capital, profile=args.profile)
    preset = get_profile_preset(args.profile)
    all_symbols = sorted({symbol for strategy in strategies for symbol in strategy.symbols})
    all_timeframes = sorted({strategy.timeframe for strategy in strategies})

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

    engine = BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        timeframes=all_timeframes,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=args.capital,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", ".\\data")),
        slippage_model=slippage,
        fee_model=fees,
    )
    results = engine.run()

    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=args.capital,
    )
    metrics = calc.calculate_all()

    print("\n" + "=" * 72)
    print(f"V8 PROFILE BACKTEST — {args.profile.upper()} ({preset['label']})")
    print("=" * 72)
    print(f"Period:           {start_date} → {end_date}")
    print(f"Core Fraction:    {preset['core_fraction']:.0%}")
    print(f"Lab Allocations:  {preset['lab_allocations'] or '{}'}")
    print(f"Strategies:       {len(strategies)}")
    print(f"Symbols:          {', '.join(all_symbols)}")
    print(f"Initial Capital:  {args.capital:.2f} USDT")
    print(f"Final Equity:     {metrics['final_equity']:.2f} USDT")
    print(f"Total Return:     {metrics['total_return']:.2%}")
    print(f"Ann. Return:      {metrics['annualized_return']:.2%}")
    print(f"Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown_pct']:.2%}")
    print(f"Win Rate:         {metrics['win_rate']:.1%}")
    print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"Total Trades:     {metrics['total_trades']}")
    print("=" * 72)

    report_dir = Path(config.get("paths", {}).get("results_dir", ".\\data\\backtest_results")) / "v8_profiles" / args.profile
    report = ReportGenerator()
    report_path = report.generate(
        metrics=metrics,
        run_id=results["run_id"],
        output_dir=str(report_dir),
    )
    logger.info(
        "v8_profile_backtest_complete",
        profile=args.profile,
        strategy_count=len(strategies),
        final_equity=metrics["final_equity"],
        sharpe=metrics["sharpe_ratio"],
        max_drawdown_pct=metrics["max_drawdown_pct"],
        report_path=str(report_path),
    )
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
