#!/usr/bin/env python3
"""Run a 1-month backtest with detailed trade records.

Usage:
    python scripts/run_1month_backtest.py
    python scripts/run_1month_backtest.py --start 2024-03-01 --end 2024-04-01
    python scripts/run_1month_backtest.py --strategy trend_donchian
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.backtest.engine import BacktestEngine
from bot.backtest.metrics import MetricsCalculator
from bot.config.env import load_env
from bot.config.loader import load_config
from bot.core.logger import setup_logging
from bot.data.storage import ParquetStorage
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.strategy.registry import StrategyRegistry, register_default_strategies
from bot.utils.time_utils import datetime_to_ms, parse_date


def format_side(side) -> str:
    return "BUY" if str(side).endswith("BUY") else "SELL"


def ms_to_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 1-month backtest with trade log")
    parser.add_argument("--env", default="backtest")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--start", default="2024-11-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--strategy", help="Run specific strategy only")
    parser.add_argument("--csv", help="Export trades to CSV file")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment=args.env)
    setup_logging("WARNING", json_format=False)

    register_default_strategies()

    start_ms = datetime_to_ms(parse_date(args.start))
    end_ms = datetime_to_ms(parse_date(args.end))

    strategy_configs = config.get("strategies", {})
    if args.strategy:
        strategy_configs = {args.strategy: strategy_configs.get(args.strategy, {})}

    strategies = StrategyRegistry.create_all(strategy_configs)
    if not strategies:
        print("ERROR: No strategies created. Check config/strategies.yaml")
        return

    all_symbols = list(set(sym for s in strategies for sym in s.symbols))
    all_timeframes = list(set(s.timeframe for s in strategies))

    print(f"Strategies: {[s.name for s in strategies]}")
    print(f"Symbols:    {all_symbols}")
    print(f"Timeframes: {all_timeframes}")
    print(f"Period:     {args.start} → {args.end}")
    print()

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

    initial_capital = config.get("initial_capital", 10000)

    engine = BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        timeframes=all_timeframes,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=initial_capital,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=slippage,
        fee_model=fees,
    )

    print("Running backtest...")
    results = engine.run()

    fills = results["fills"]

    # ── Summary ──────────────────────────────────────────────
    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=fills,
        initial_capital=initial_capital,
    )
    metrics = calc.calculate_all()

    print()
    print("=" * 72)
    print("  BACKTEST RESULTS")
    print("=" * 72)
    print(f"  Run ID:           {results['run_id']}")
    print(f"  Period:           {args.start} → {args.end}")
    print(f"  Symbols:          {', '.join(all_symbols)}")
    print(f"  Strategies:       {', '.join(s.name for s in strategies)}")
    print(f"  Initial Capital:  {initial_capital:,.2f} USDT")
    print(f"  Final Equity:     {metrics['final_equity']:,.2f} USDT")
    print(f"  Total Return:     {metrics['total_return']:.2%}")
    print(f"  Ann. Return:      {metrics['annualized_return']:.2%}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"  Sortino Ratio:    {metrics.get('sortino_ratio', 0):.2f}")
    print(f"  Max Drawdown:     {metrics['max_drawdown_pct']:.2%}")
    print(f"  Win Rate:         {metrics['win_rate']:.1%}")
    print(f"  Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"  Total Trades:     {metrics['total_trades']}")
    print(f"  Total Commission: {sum(f.commission for f in fills):,.2f} USDT")
    print("=" * 72)

    # ── Per-strategy breakdown ───────────────────────────────
    strategy_fills: dict[str, list] = {}
    for f in fills:
        strategy_fills.setdefault(f.strategy_name, []).append(f)

    print()
    print("  PER-STRATEGY BREAKDOWN")
    print("-" * 72)
    print(f"  {'Strategy':<25} {'Trades':>7} {'Buy':>5} {'Sell':>5} {'Commission':>12} {'PnL':>12}")
    print("-" * 72)
    for sname, sfills in sorted(strategy_fills.items()):
        buys = sum(1 for f in sfills if format_side(f.side) == "BUY")
        sells = sum(1 for f in sfills if format_side(f.side) == "SELL")
        total_comm = sum(f.commission for f in sfills)
        total_pnl = sum(f.realized_pnl for f in sfills)
        print(f"  {sname:<25} {len(sfills):>7} {buys:>5} {sells:>5} {total_comm:>12,.2f} {total_pnl:>12,.2f}")
    print("-" * 72)

    # ── Detailed trade log ───────────────────────────────────
    if fills:
        print()
        print("  DETAILED TRADE LOG")
        print("-" * 120)
        header = f"  {'#':>4} {'Timestamp':<18} {'Strategy':<22} {'Symbol':<12} {'Side':<5} {'Qty':>12} {'Price':>12} {'Notional':>14} {'Commission':>11} {'PnL':>12}"
        print(header)
        print("-" * 120)

        for i, f in enumerate(fills, 1):
            ts = f.timestamp.strftime("%Y-%m-%d %H:%M") if hasattr(f.timestamp, 'strftime') else str(f.timestamp)[:16]
            side = format_side(f.side)
            notional = f.quantity * f.price
            print(
                f"  {i:>4} {ts:<18} {f.strategy_name:<22} {f.symbol:<12} {side:<5} "
                f"{f.quantity:>12.6f} {f.price:>12.2f} {notional:>14,.2f} "
                f"{f.commission:>11.4f} {f.realized_pnl:>12.4f}"
            )

        print("-" * 120)
        print(f"  Total: {len(fills)} fills")
    else:
        print("\n  No trades executed during this period.")

    # ── Equity curve summary ─────────────────────────────────
    eq = results["equity_curve"]
    if eq:
        print()
        print("  EQUITY CURVE (sampled)")
        print("-" * 50)
        step = max(1, len(eq) // 20)
        for j in range(0, len(eq), step):
            ts_ms, equity = eq[j]
            print(f"  {ms_to_str(ts_ms)}  {equity:>12,.2f} USDT")
        if (len(eq) - 1) % step != 0:
            ts_ms, equity = eq[-1]
            print(f"  {ms_to_str(ts_ms)}  {equity:>12,.2f} USDT")
        print("-" * 50)

    # ── CSV export ───────────────────────────────────────────
    if args.csv and fills:
        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                "timestamp", "strategy", "symbol", "side", "quantity",
                "price", "notional", "commission", "realized_pnl", "order_id",
            ])
            for f in fills:
                ts = f.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(f.timestamp, 'strftime') else str(f.timestamp)
                writer.writerow([
                    ts, f.strategy_name, f.symbol, format_side(f.side),
                    f.quantity, f.price, f.quantity * f.price,
                    f.commission, f.realized_pnl, f.order_id,
                ])
        print(f"\n  Trades exported to: {csv_path}")


if __name__ == "__main__":
    main()
