#!/usr/bin/env python3
"""Quick 90-day backtest to validate position sizing fixes."""
from __future__ import annotations
import sys, json, itertools
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

INITIAL_CAPITAL = 150.0
START = "2024-01-01"
END = "2024-03-31"  # 90 days only

PARAM_GRID = {
    "leverage": [1, 2],
    "trend_risk_pct": [0.5, 1.0, 1.5],
    "bb_stop_loss_pct": [1.0, 1.5, 2.0],
    "bb_long_only": [True, False],
    "alloc_trend": [0.0, 0.50, 1.0],  # fewer allocations for speed
}


def run_one(config, start_ms, end_ms, leverage, trend_risk, bb_stop, bb_long_only, alloc_trend):
    import copy
    config = copy.deepcopy(config)
    register_default_strategies()
    sc = config.get("strategies", {})

    sc["funding_arb"]["enabled"] = False
    sc["grid_futures"]["enabled"] = False

    alloc_bb = round(1.0 - alloc_trend, 2)
    sc["trend_donchian"]["enabled"] = alloc_trend > 0
    sc["mean_reversion_bb"]["enabled"] = alloc_bb > 0
    sc["trend_donchian"]["leverage"] = leverage
    sc["mean_reversion_bb"]["leverage"] = leverage
    sc["trend_donchian"]["risk_per_trade_pct"] = trend_risk
    sc["mean_reversion_bb"]["stop_loss_pct"] = bb_stop
    sc["mean_reversion_bb"]["long_only"] = bb_long_only

    strategies = StrategyRegistry.create_all(sc)
    if not strategies:
        return None

    all_symbols = sorted({sym for s in strategies for sym in s.symbols})

    exec_cfg = config.get("execution", {})
    risk_limits = config.get("risk_limits", {})
    cb_cfg = {
        "bar_change_threshold_pct": risk_limits.get("circuit_breaker_bar_pct", 5.0),
        "cooldown_minutes": risk_limits.get("circuit_breaker_cooldown_min", 30),
    }

    engine = BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=INITIAL_CAPITAL,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=SlippageModel(
            model_type=exec_cfg.get("slippage_model", "fixed_bps"),
            fixed_bps=exec_cfg.get("slippage_bps", 2),
            bps_base=exec_cfg.get("slippage_bps_base", 1),
        ),
        fee_model=FeeModel(
            maker_rate=exec_cfg.get("fee_rate_maker", 0.0002),
            taker_rate=exec_cfg.get("fee_rate_taker", 0.0004),
        ),
        circuit_breaker_config=cb_cfg,
    )

    results = engine.run()
    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=INITIAL_CAPITAL,
    )
    return calc.calculate_all()


def main():
    load_env()
    config = load_config("config/config.yaml", environment="backtest")
    setup_logging("WARNING", json_format=False)

    start_ms = datetime_to_ms(parse_date(START))
    end_ms = datetime_to_ms(parse_date(END))

    combos = list(itertools.product(
        PARAM_GRID["leverage"],
        PARAM_GRID["trend_risk_pct"],
        PARAM_GRID["bb_stop_loss_pct"],
        PARAM_GRID["bb_long_only"],
        PARAM_GRID["alloc_trend"],
    ))
    print(f"Testing {len(combos)} parameter combinations (90-day)...\n")

    results = []
    for i, (lev, tr, bs, blo, at) in enumerate(combos, 1):
        try:
            m = run_one(config, start_ms, end_ms, lev, tr, bs, blo, at)
            if m is None:
                continue
            entry = {
                "leverage": lev,
                "trend_risk_pct": tr,
                "bb_stop_loss_pct": bs,
                "bb_long_only": blo,
                "alloc_trend": at,
                "alloc_bb": round(1.0 - at, 2),
                "total_return": m.get("total_return", -999),
                "sharpe": m.get("sharpe_ratio", 0),
                "max_dd": m.get("max_drawdown_pct", -999),
                "trades": m.get("total_trades", 0),
                "final_equity": m.get("final_equity", 0),
            }
            results.append(entry)
        except Exception as e:
            print(f"  ERROR combo {i}: {e}")
        if i % 10 == 0:
            print(f"  Progress: {i}/{len(combos)}")

    # Check differentiation
    returns = [r["total_return"] for r in results]
    unique_returns = len(set(f"{r:.6f}" for r in returns))
    print(f"\n{'='*80}")
    print(f"DIFFERENTIATION CHECK: {unique_returns} unique returns out of {len(results)} combos")
    print(f"{'='*80}")

    if unique_returns <= 3:
        print("WARNING: Most combos still give same result!")
    else:
        print("SUCCESS: Parameters now produce differentiated results!")

    # Sort by return
    results.sort(key=lambda r: r["total_return"], reverse=True)

    # Show one representative per unique return group
    seen = set()
    unique_results = []
    for r in results:
        key = f"{r['total_return']:.6f}"
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    print(f"\n--- ALL {len(unique_results)} UNIQUE GROUPS (sorted by return) ---")
    for i, r in enumerate(unique_results, 1):
        print(f"  #{i}: Return {r['total_return']:>8.2%} | Sharpe {r['sharpe']:>6.2f} | "
              f"MaxDD {r['max_dd']:>8.2%} | Trades {r['trades']:>3} | "
              f"Equity {r['final_equity']:>8.2f}")
        print(f"       Lev={r['leverage']} TrendRisk={r['trend_risk_pct']}% BBStop={r['bb_stop_loss_pct']}% "
              f"LongOnly={r['bb_long_only']} trend={r['alloc_trend']:.0%} bb={r['alloc_bb']:.0%}")

    print(f"\n--- BOTTOM 5 ---")
    for i, r in enumerate(results[-5:], 1):
        print(f"  #{i}: Return {r['total_return']:>8.2%} | Trades {r['trades']:>3} | "
              f"Lev={r['leverage']} LO={r['bb_long_only']} trend={r['alloc_trend']:.0%} bb={r['alloc_bb']:.0%}")


if __name__ == "__main__":
    main()
