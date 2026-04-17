#!/usr/bin/env python3
"""Run backtest with specified allocation and print detailed fill-level analysis."""
from __future__ import annotations
import sys
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

def main() -> None:
    load_env()
    config = load_config("config/config.yaml", environment="backtest")
    setup_logging("WARNING", json_format=False)

    start_ms = datetime_to_ms(parse_date("2024-01-01"))
    end_ms = datetime_to_ms(parse_date("2024-12-31"))
    initial_capital = 150.0

    # Best allocation from optimization
    allocation = {
        "funding_arb": 0.0,
        "trend_donchian": 0.25,
        "grid_futures": 0.0,
        "mean_reversion_bb": 0.75,
    }

    register_default_strategies()
    strategy_configs = config.get("strategies", {})
    for name, pct in allocation.items():
        if name in strategy_configs:
            strategy_configs[name]["enabled"] = pct > 0

    strategies = StrategyRegistry.create_all(strategy_configs)
    all_symbols = sorted({sym for s in strategies for sym in s.symbols})

    exec_cfg = config.get("execution", {})
    risk_limits = config.get("risk_limits", {})
    cb_cfg = {
        "bar_change_threshold_pct": risk_limits.get("circuit_breaker_bar_pct", 5.0),
        "cooldown_minutes": risk_limits.get("circuit_breaker_cooldown_min", 30),
    }
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
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=initial_capital,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=slippage,
        fee_model=fees,
        circuit_breaker_config=cb_cfg,
    )

    results = engine.run()

    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=initial_capital,
    )
    metrics = calc.calculate_all()

    # ── Summary ──
    print("=" * 70)
    print("BEST ALLOCATION BACKTEST (trend_donchian 25% + mean_reversion_bb 75%)")
    print("=" * 70)
    print(f"Initial Capital:  {initial_capital:.2f} USDT")
    print(f"Final Equity:     {metrics['final_equity']:.2f} USDT")
    print(f"Total Return:     {metrics['total_return']:.2%}")
    print(f"Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown_pct']:.2%}")
    print(f"Win Rate:         {metrics['win_rate']:.1%}")
    print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"Total Trades:     {metrics['total_trades']}")

    # ── Monthly ──
    monthly = metrics.get("monthly_returns", [])
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    print("\n" + "=" * 72)
    print("MONTHLY PROFIT")
    print("=" * 72)
    print(f"{'Month':<12} {'Start':>12} {'End':>12} {'Profit':>12} {'Return':>10}")
    print("-" * 72)
    for m in monthly:
        label = f"{m['year']}-{month_names[m['month']]}"
        sign = "+" if m["profit"] >= 0 else ""
        print(f"{label:<12} {m['start_equity']:>12.2f} {m['end_equity']:>12.2f} "
              f"{sign}{m['profit']:>11.2f} {m['return_pct']:>9.2%}")
    if monthly:
        total_profit = monthly[-1]["end_equity"] - initial_capital
        total_ret = total_profit / initial_capital
        print("-" * 72)
        print(f"{'TOTAL':<12} {initial_capital:>12.2f} {monthly[-1]['end_equity']:>12.2f} "
              f"{'+' if total_profit >= 0 else ''}{total_profit:>11.2f} {total_ret:>9.2%}")
    print("=" * 72)

    # ── Equity curve drawdown analysis ──
    ec = results["equity_curve"]
    if ec:
        peak = ec[0][1]
        worst_dd = 0.0
        worst_dd_time = ec[0][0]
        peak_time = ec[0][0]
        for ts, eq in ec:
            if eq > peak:
                peak = eq
                peak_time = ts
            dd = (eq - peak) / peak
            if dd < worst_dd:
                worst_dd = dd
                worst_dd_time = ts
        from bot.utils.time_utils import ms_to_datetime
        print(f"\nPeak equity:    {peak:.2f} USDT at {ms_to_datetime(peak_time)}")
        print(f"Worst drawdown: {worst_dd:.2%} at {ms_to_datetime(worst_dd_time)}")

    # ── Fill summary ──
    fills = results["fills"]
    print(f"\n{'='*72}")
    print(f"FILL SUMMARY ({len(fills)} fills)")
    print(f"{'='*72}")
    total_commission = 0.0
    total_rpnl = 0.0
    for f in fills:
        total_commission += getattr(f, "commission", 0)
        total_rpnl += getattr(f, "realized_pnl", 0)
    print(f"Total commissions: {total_commission:.4f} USDT")
    print(f"Total realized PnL: {total_rpnl:.4f} USDT")

    # Show first 20 and last 20 fills
    print(f"\nFirst 10 fills:")
    for f in fills[:10]:
        print(f"  {f.timestamp} | {f.strategy_name:<20} | {f.symbol:<10} | "
              f"{f.side.value:<5} | qty={f.quantity:.6f} | px={f.price:.2f} | "
              f"fee={f.commission:.4f} | rpnl={f.realized_pnl:.4f}")
    if len(fills) > 20:
        print(f"\n  ... ({len(fills) - 20} fills omitted) ...\n")
        print(f"Last 10 fills:")
        for f in fills[-10:]:
            print(f"  {f.timestamp} | {f.strategy_name:<20} | {f.symbol:<10} | "
                  f"{f.side.value:<5} | qty={f.quantity:.6f} | px={f.price:.2f} | "
                  f"fee={f.commission:.4f} | rpnl={f.realized_pnl:.4f}")


if __name__ == "__main__":
    main()
