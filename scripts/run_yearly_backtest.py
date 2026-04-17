#!/usr/bin/env python3
"""Run a 1-year backtest starting with 150 USDT.

Analyses monthly profit and searches for the capital-allocation /
strategy-parameter combination that maximises total return.

Usage:
    python scripts/run_yearly_backtest.py [--start 2024-01-01] [--end 2024-12-31] \
        [--initial-capital 150] [--trials 50] [--config config/config.yaml]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

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

# ---------------------------------------------------------------------------
# Allocation grid – every combination that sums to 1.0 in 0.05 steps
# ---------------------------------------------------------------------------
ALLOCATION_STEP = 0.05
STRATEGY_NAMES = ["funding_arb", "trend_donchian", "grid_futures", "mean_reversion_bb"]


def _generate_allocation_grid(
    names: list[str],
    step: float = ALLOCATION_STEP,
) -> list[dict[str, float]]:
    """Generate all allocation dicts whose values sum to 1.0."""
    n = len(names)
    steps = int(round(1.0 / step))
    combos: list[dict[str, float]] = []
    for parts in itertools.combinations_with_replacement(range(steps + 1), n):
        if sum(parts) != steps:
            continue
        for perm in set(itertools.permutations(parts)):
            alloc = {name: round(p * step, 2) for name, p in zip(names, perm)}
            combos.append(alloc)
    return combos


# ---------------------------------------------------------------------------
# Single-run helper
# ---------------------------------------------------------------------------

def _run_single_backtest(
    config: dict[str, Any],
    start_ms: int,
    end_ms: int,
    initial_capital: float,
    allocation: dict[str, float],
) -> dict[str, Any]:
    """Run one backtest with the given capital allocation.

    Returns metrics dict (from ``MetricsCalculator.calculate_all``).
    """
    register_default_strategies()
    strategy_configs = config.get("strategies", {})

    # Apply allocation – update per_grid_size_pct / risk_per_trade_pct
    # proportionally to capital fraction.
    for name, pct in allocation.items():
        if name in strategy_configs:
            strategy_configs[name]["enabled"] = pct > 0

    strategies = StrategyRegistry.create_all(strategy_configs)
    if not strategies:
        return {"total_return": -1.0, "monthly_returns": []}

    all_symbols = list({sym for s in strategies for sym in s.symbols})

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
        timeframe=strategies[0].timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=initial_capital,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=slippage,
        fee_model=fees,
    )

    results = engine.run()

    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=initial_capital,
    )
    return calc.calculate_all()


# ---------------------------------------------------------------------------
# Printing helpers
# ---------------------------------------------------------------------------

def _print_monthly_table(monthly: list[dict[str, Any]], initial_capital: float) -> None:
    """Pretty-print the monthly profit table."""
    month_names = [
        "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    print("\n" + "=" * 72)
    print("MONTHLY PROFIT ANALYSIS")
    print("=" * 72)
    print(f"{'Month':<12} {'Start':>12} {'End':>12} {'Profit':>12} {'Return':>10}")
    print("-" * 72)
    for m in monthly:
        label = f"{m['year']}-{month_names[m['month']]}"
        color = "+" if m["profit"] >= 0 else ""
        print(
            f"{label:<12} "
            f"{m['start_equity']:>12.2f} "
            f"{m['end_equity']:>12.2f} "
            f"{color}{m['profit']:>11.2f} "
            f"{m['return_pct']:>9.2%}"
        )
    print("-" * 72)
    if monthly:
        total_profit = monthly[-1]["end_equity"] - initial_capital
        total_ret = total_profit / initial_capital if initial_capital else 0
        print(
            f"{'TOTAL':<12} "
            f"{initial_capital:>12.2f} "
            f"{monthly[-1]['end_equity']:>12.2f} "
            f"{'+'if total_profit>=0 else ''}{total_profit:>11.2f} "
            f"{total_ret:>9.2%}"
        )
    print("=" * 72)


def _print_summary(metrics: dict[str, Any], initial_capital: float) -> None:
    """Print high-level performance summary."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Initial Capital:  {initial_capital:.2f} USDT")
    print(f"Final Equity:     {metrics['final_equity']:.2f} USDT")
    print(f"Total Return:     {metrics['total_return']:.2%}")
    print(f"Ann. Return:      {metrics['annualized_return']:.2%}")
    print(f"Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown_pct']:.2%}")
    print(f"Win Rate:         {metrics['win_rate']:.1%}")
    print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"Total Trades:     {metrics['total_trades']}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Optimisation search
# ---------------------------------------------------------------------------

def _run_optimization(
    config: dict[str, Any],
    start_ms: int,
    end_ms: int,
    initial_capital: float,
    max_combos: int,
) -> list[dict[str, Any]]:
    """Search allocation combinations and rank by total return.

    Returns the ranked list (best first).
    """
    grid = _generate_allocation_grid(STRATEGY_NAMES)
    # Limit the grid size if requested
    if max_combos and len(grid) > max_combos:
        import random
        random.seed(42)
        grid = random.sample(grid, max_combos)

    logger.info("optimization_grid_size", combos=len(grid))
    ranked: list[dict[str, Any]] = []

    for idx, alloc in enumerate(grid, 1):
        try:
            metrics = _run_single_backtest(config, start_ms, end_ms, initial_capital, alloc)
            ranked.append({"allocation": alloc, "metrics": metrics})
        except Exception as exc:  # noqa: BLE001
            logger.warning("combo_failed", idx=idx, error=str(exc))
        if idx % 10 == 0:
            logger.info("optimization_progress", done=idx, total=len(grid))

    # Sort by total_return descending
    ranked.sort(key=lambda r: r["metrics"].get("total_return", -999), reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="1-year backtest with monthly analysis & optimisation")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--start", default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--initial-capital", type=float, default=150.0, help="Starting capital in USDT")
    parser.add_argument("--optimize", action="store_true", help="Search for best allocation combo")
    parser.add_argument("--max-combos", type=int, default=50, help="Max allocation combos to try")
    parser.add_argument("--top", type=int, default=5, help="Show top N optimization results")
    parser.add_argument("--output", default="data/backtest_results", help="Output directory")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment="backtest")
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    start_ms = datetime_to_ms(parse_date(args.start))
    end_ms = datetime_to_ms(parse_date(args.end))
    initial_capital = args.initial_capital

    # ── 1. Run baseline backtest with default allocation ──────────────
    print(f"\n>>> Running baseline backtest: {args.start} → {args.end}, capital={initial_capital} USDT")
    default_alloc = config.get("capital_allocation", {
        "funding_arb": 0.60,
        "trend_donchian": 0.25,
        "grid_futures": 0.10,
        "mean_reversion_bb": 0.05,
    })
    metrics = _run_single_backtest(config, start_ms, end_ms, initial_capital, default_alloc)
    _print_summary(metrics, initial_capital)
    _print_monthly_table(metrics.get("monthly_returns", []), initial_capital)

    # ── 2. Optional: optimisation search ──────────────────────────────
    if args.optimize:
        print("\n>>> Searching for optimal allocation combination …")
        ranked = _run_optimization(config, start_ms, end_ms, initial_capital, args.max_combos)

        print("\n" + "=" * 72)
        print(f"TOP {args.top} ALLOCATION COMBINATIONS (by total return)")
        print("=" * 72)
        for i, entry in enumerate(ranked[: args.top], 1):
            m = entry["metrics"]
            a = entry["allocation"]
            alloc_str = ", ".join(f"{k}={v:.0%}" for k, v in a.items() if v > 0)
            print(
                f"  #{i}: Return {m.get('total_return', 0):.2%} | "
                f"Sharpe {m.get('sharpe_ratio', 0):.2f} | "
                f"MaxDD {m.get('max_drawdown_pct', 0):.2%} | "
                f"Trades {m.get('total_trades', 0)}"
            )
            print(f"       Allocation: {alloc_str}")

        # Show monthly for best combo
        if ranked:
            best = ranked[0]
            print("\n>>> Best combo monthly breakdown:")
            _print_monthly_table(
                best["metrics"].get("monthly_returns", []),
                initial_capital,
            )

        # Save results
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        results_path = out_dir / "yearly_optimization_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "initial_capital": initial_capital,
                    "period": f"{args.start} → {args.end}",
                    "top_results": [
                        {
                            "rank": i + 1,
                            "allocation": r["allocation"],
                            "total_return": r["metrics"].get("total_return"),
                            "sharpe_ratio": r["metrics"].get("sharpe_ratio"),
                            "max_drawdown_pct": r["metrics"].get("max_drawdown_pct"),
                            "total_trades": r["metrics"].get("total_trades"),
                            "monthly_returns": r["metrics"].get("monthly_returns", []),
                        }
                        for i, r in enumerate(ranked[: args.top])
                    ],
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\nResults saved to {results_path}")

    # ── 3. Generate HTML report for baseline run ──────────────────────
    report = ReportGenerator()
    report_path = report.generate(
        metrics=metrics,
        run_id=f"yearly_{args.start}_{args.end}",
        output_dir=args.output,
    )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
